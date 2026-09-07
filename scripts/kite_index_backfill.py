#!/usr/bin/env python3
"""
One-time Kite Connect INDEX history backfill for the market-breadth store.

The stock price store already goes back to 2019, but the index files only start
2024-04-01, so nifty_close is missing on ~1,300 older sessions and every
divergence flag is blind across the 2020 crash and 2022 correction.

This pulls daily index candles from BACKFILL_START up to the day BEFORE the
existing index history begins, and folds them into:

  data/indices.parquet     columns: date, nifty_close, niftynext50,
                                     midcap150_close, nifty500_close
  data/index_ohlc.parquet  columns: date, index, open, high, low, close
                                     index in {NIFTY50, BANKNIFTY, INDIAVIX}

Existing rows always win on any date overlap, so recent good data is never
touched. Run on GitHub Actions (workflow_dispatch), NOT on the user's machine.

Env vars (set by the workflow):
  KITE_API_KEY, KITE_API_SECRET   from GitHub encrypted secrets
  KITE_REQUEST_TOKEN              pasted by the user when triggering the run
  BACKFILL_START                  optional, default 2019-01-01
"""

import os, sys, time
from datetime import date, datetime, timedelta
import pandas as pd
from kiteconnect import KiteConnect

DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data")
IDX_STORE = os.path.join(DATA, "indices.parquet")
OHLC_STORE = os.path.join(DATA, "index_ohlc.parquet")
START = os.environ.get("BACKFILL_START", "2019-01-01")

API_KEY = os.environ["KITE_API_KEY"]
API_SECRET = os.environ["KITE_API_SECRET"]
REQUEST_TOKEN = os.environ["KITE_REQUEST_TOKEN"].strip()

# indices.parquet close columns -> acceptable Kite index names (first match wins)
CLOSE_TARGETS = {
    "nifty_close":     ["NIFTY 50"],
    "niftynext50":     ["NIFTY NEXT 50"],
    "midcap150_close": ["NIFTY MIDCAP 150", "NIFTY MID 150"],
    "nifty500_close":  ["NIFTY 500"],
}
# index_ohlc.parquet tag -> acceptable Kite index names
OHLC_TARGETS = {
    "NIFTY50":   ["NIFTY 50"],
    "BANKNIFTY": ["NIFTY BANK", "BANKNIFTY"],
    "INDIAVIX":  ["INDIA VIX", "INDIAVIX"],
}
IDX_COLS = ["date", "nifty_close", "niftynext50", "midcap150_close", "nifty500_close"]
OHLC_COLS = ["date", "index", "open", "high", "low", "close"]


def log(m): print(m, flush=True)


def chunks(d_from, d_to, span=1900):
    """Kite caps day-candle requests at 2000 days; yield <=span windows."""
    cur = d_from
    while cur <= d_to:
        end = min(cur + timedelta(days=span), d_to)
        yield cur, end
        cur = end + timedelta(days=1)


def pull_day(kite, token, d_from, d_to):
    frames = []
    for a, b in chunks(d_from, d_to):
        for attempt in range(4):
            try:
                c = kite.historical_data(
                    int(token),
                    datetime.combine(a, datetime.min.time()),
                    datetime.combine(b, datetime.min.time()),
                    "day")
                if c:
                    frames.append(pd.DataFrame(c))
                break
            except Exception as e:
                if attempt == 3:
                    raise
                time.sleep(1.5 * (attempt + 1))
        time.sleep(0.34)
    if not frames:
        return pd.DataFrame(columns=["date", "open", "high", "low", "close"])
    df = pd.concat(frames, ignore_index=True)
    df["date"] = pd.to_datetime(df["date"]).dt.tz_localize(None).dt.normalize()
    return df.drop_duplicates("date")


def resolve(index_master, names):
    for n in names:
        hit = index_master[index_master["_key"] == n.strip().upper()]
        if len(hit):
            return int(hit.iloc[0]["instrument_token"]), hit.iloc[0]["tradingsymbol"]
    return None, None


def main():
    kite = KiteConnect(api_key=API_KEY)
    try:
        sess = kite.generate_session(REQUEST_TOKEN, api_secret=API_SECRET)
    except Exception as e:
        log("LOGIN FAILED. request_token is single-use and expires within minutes.")
        log(f"Re-open the login link, grab a fresh token, re-run. Kite said: {e}")
        sys.exit(1)
    kite.set_access_token(sess["access_token"])
    log("Login OK.")

    start = datetime.strptime(START, "%Y-%m-%d").date()

    # Backfill only the gap BEFORE the existing index history.
    if os.path.exists(IDX_STORE):
        cut = pd.to_datetime(pd.read_parquet(IDX_STORE, columns=["date"])["date"]).min().date()
    else:
        cut = date.today()
    end = cut - timedelta(days=1)
    if start > end:
        log(f"Nothing to do: index history already starts {cut} (<= {start}).")
        return
    log(f"Backfilling index history {start} -> {end} (existing starts {cut}).")

    # Index instrument master (segment INDICES on NSE).
    inst = pd.DataFrame(kite.instruments("NSE"))
    ix = inst[inst["segment"] == "INDICES"].copy()
    ix["_key"] = ix["tradingsymbol"].str.strip().str.upper()
    log(f"{len(ix)} NSE indices in master.")

    # ---- closes for indices.parquet ----
    close_frames = []
    for col, names in CLOSE_TARGETS.items():
        tok, ts = resolve(ix, names)
        if tok is None:
            log(f"  WARN no index match for {col} (tried {names}); column stays NaN.")
            continue
        df = pull_day(kite, tok, start, end)[["date", "close"]].rename(columns={"close": col})
        log(f"  {col:<16} <- '{ts}': {len(df)} sessions")
        close_frames.append(df.set_index("date"))
    if close_frames:
        closes = pd.concat(close_frames, axis=1).reset_index()
        for c in IDX_COLS:
            if c not in closes.columns:
                closes[c] = pd.NA
        closes = closes[IDX_COLS]
    else:
        closes = pd.DataFrame(columns=IDX_COLS)

    # ---- OHLC for index_ohlc.parquet ----
    ohlc_frames = []
    for tag, names in OHLC_TARGETS.items():
        tok, ts = resolve(ix, names)
        if tok is None:
            log(f"  WARN no index match for OHLC {tag} (tried {names}); skipped.")
            continue
        try:
            df = pull_day(kite, tok, start, end)
        except Exception as e:
            log(f"  WARN OHLC {tag} pull failed ({e}); skipped (common for INDIA VIX).")
            continue
        if not len(df):
            log(f"  WARN OHLC {tag}: no candles returned; skipped.")
            continue
        df = df[["date", "open", "high", "low", "close"]].copy()
        df["index"] = tag
        log(f"  OHLC {tag:<10} <- '{ts}': {len(df)} sessions")
        ohlc_frames.append(df[OHLC_COLS])
    ohlc = pd.concat(ohlc_frames, ignore_index=True) if ohlc_frames else pd.DataFrame(columns=OHLC_COLS)

    # ---- merge under existing, existing wins on any overlap ----
    if os.path.exists(IDX_STORE):
        old = pd.read_parquet(IDX_STORE)
        old["date"] = pd.to_datetime(old["date"]).dt.normalize()
        for c in IDX_COLS:
            if c not in old.columns: old[c] = pd.NA
            if c not in closes.columns: closes[c] = pd.NA
        merged = pd.concat([old[IDX_COLS], closes[IDX_COLS]], ignore_index=True)
        merged = merged.drop_duplicates("date", keep="first").sort_values("date")
    else:
        merged = closes.sort_values("date")
    merged.to_parquet(IDX_STORE, index=False)

    if os.path.exists(OHLC_STORE):
        oldo = pd.read_parquet(OHLC_STORE)
        oldo["date"] = pd.to_datetime(oldo["date"]).dt.normalize()
        mo = pd.concat([oldo[OHLC_COLS], ohlc[OHLC_COLS]], ignore_index=True)
        mo = mo.drop_duplicates(["date", "index"], keep="first").sort_values(["index", "date"])
    else:
        mo = ohlc.sort_values(["index", "date"])
    mo.to_parquet(OHLC_STORE, index=False)

    log(f"DONE. indices.parquet now spans "
        f"{pd.to_datetime(merged['date']).min().date()} -> {pd.to_datetime(merged['date']).max().date()}, "
        f"{merged['date'].nunique()} sessions.")
    log(f"index_ohlc.parquet tags: {sorted(mo['index'].unique())}, "
        f"{mo['date'].nunique()} sessions.")


if __name__ == "__main__":
    main()
