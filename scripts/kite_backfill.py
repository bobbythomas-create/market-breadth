#!/usr/bin/env python3
"""
One-time Kite Connect historical backfill for the market-breadth price store.

Pulls daily OHLCV from BACKFILL_START up to the day before each symbol's
existing coverage, chunked under Kite's 2000-day/request cap, and folds it
into data/prices.parquet in the schema ingest.py expects.

Env vars (set by the workflow):
  KITE_API_KEY, KITE_API_SECRET, KITE_REQUEST_TOKEN
  BACKFILL_START      optional, default 2019-01-01
  BACKFILL_UNIVERSE   'store' (default) = symbols already in the store (~2.6k)
                      'all'             = every NSE EQ symbol (~18k, ~3h)
"""

import os, sys, time
from datetime import date, datetime, timedelta

import pandas as pd
from kiteconnect import KiteConnect

DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data")
STORE = os.path.join(DATA, "prices.parquet")
START = os.environ.get("BACKFILL_START", "2019-01-01")
UNIVERSE = os.environ.get("BACKFILL_UNIVERSE", "store").strip().lower()
CHUNK_DAYS = 1950          # per-request span, safely under Kite's 2000-day cap

API_KEY = os.environ["KITE_API_KEY"]
API_SECRET = os.environ["KITE_API_SECRET"]
REQUEST_TOKEN = os.environ["KITE_REQUEST_TOKEN"].strip()

STORE_COLS = ["date", "symbol", "open", "high", "low", "close",
              "prev_close", "volume", "turnover"]


def log(m):
    print(m, flush=True)


def pull_symbol(kite, tok, d_from, d_to):
    """Pull day candles across [d_from, d_to] in <2000-day chunks, throttled
    and retried. Returns (candles_list, clean_bool). clean=False if any chunk
    failed hard after retries."""
    out, seg, clean = [], d_from, True
    while seg <= d_to:
        seg_end = min(seg + timedelta(days=CHUNK_DAYS), d_to)
        for attempt in range(4):
            try:
                c = kite.historical_data(
                    tok,
                    datetime.combine(seg, datetime.min.time()),
                    datetime.combine(seg_end, datetime.min.time()),
                    "day")
                if c:
                    out.extend(c)
                break
            except Exception as e:
                if attempt == 3:
                    clean = False
                    log(f"  chunk fail {seg}->{seg_end}: {e}")
                else:
                    time.sleep(1.5 * (attempt + 1))     # backoff on transient errors
        time.sleep(0.34)                                 # ~2.9 req/sec, per API call
        seg = seg_end + timedelta(days=1)
    return out, clean


def main():
    kite = KiteConnect(api_key=API_KEY)
    try:
        sess = kite.generate_session(REQUEST_TOKEN, api_secret=API_SECRET)
    except Exception as e:
        log("LOGIN FAILED. The request_token is single-use and expires within a")
        log("few minutes. Re-open the login link, grab a fresh token, and re-run.")
        log(f"Kite said: {e}")
        sys.exit(1)
    kite.set_access_token(sess["access_token"])
    log("Login OK.")

    start = datetime.strptime(START, "%Y-%m-%d").date()
    today = date.today()

    # What the store already covers, per symbol.
    existing_symbols, sym_min = set(), {}
    if os.path.exists(STORE):
        ex = pd.read_parquet(STORE, columns=["date", "symbol"])
        if len(ex):
            ex["date"] = pd.to_datetime(ex["date"]).dt.date
            existing_symbols = set(ex["symbol"].unique())
            sym_min = ex.groupby("symbol")["date"].min().to_dict()
            log(f"Store holds {len(existing_symbols)} symbols, "
                f"earliest date {min(sym_min.values())}.")

    log("Fetching instrument master...")
    inst = pd.DataFrame(kite.instruments("NSE"))
    eq = inst[(inst["instrument_type"] == "EQ") & (inst["segment"] == "NSE")]
    eq = eq[["instrument_token", "tradingsymbol"]].drop_duplicates("tradingsymbol")
    full_n = len(eq)

    if UNIVERSE == "store":
        if not existing_symbols:
            log("universe=store but the store is empty. Re-run with universe=all.")
            sys.exit(1)
        eq = eq[eq["tradingsymbol"].isin(existing_symbols)]
        log(f"universe=store: {full_n} NSE EQ trimmed to {len(eq)} in-store symbols.")
    else:
        log(f"universe=all: pulling all {full_n} NSE EQ symbols. Expect ~3+ hours.")

    if not len(eq):
        log("No symbols to pull after filtering. Exiting clean.")
        return

    frames, ok, fail, covered = [], 0, 0, 0
    total = len(eq)
    for i, (tok, sym) in enumerate(zip(eq["instrument_token"], eq["tradingsymbol"]), 1):
        s_min = sym_min.get(sym)
        d_to = (s_min - timedelta(days=1)) if s_min else today
        if start >= d_to:                       # already covered from START
            covered += 1
        else:
            got, clean = pull_symbol(kite, int(tok), start, d_to)
            if got:
                df = pd.DataFrame(got)
                df["symbol"] = sym
                frames.append(df[["date", "symbol", "open", "high",
                                  "low", "close", "volume"]])
                ok += 1
            elif clean:
                ok += 1                          # legitimately no data in window
            else:
                fail += 1
        if i % 250 == 0:
            log(f"  {i}/{total} done ({ok} ok, {fail} failed, {covered} already covered)...")

    if not frames:
        log("No new candles pulled (store may already cover this range). "
            "Exiting without touching the store.")
        return

    new = pd.concat(frames, ignore_index=True)
    new["date"] = pd.to_datetime(new["date"]).dt.tz_localize(None).dt.normalize()
    new = new.drop_duplicates(["symbol", "date"]).sort_values(["symbol", "date"])
    new["prev_close"] = new.groupby("symbol")["close"].shift(1)
    new["turnover"] = new["volume"] * new["close"]
    new = new.dropna(subset=["prev_close"])
    new = new[STORE_COLS]

    if os.path.exists(STORE):
        old = pd.read_parquet(STORE)
        old["date"] = pd.to_datetime(old["date"]).dt.tz_localize(None).dt.normalize()
        merged = pd.concat([old, new], ignore_index=True)
        merged = merged.drop_duplicates(subset=["date", "symbol"], keep="first")
    else:
        merged = new
    merged = merged.sort_values(["symbol", "date"])
    merged.to_parquet(STORE, index=False)

    log(f"DONE. Added {len(new)} rows across {new['symbol'].nunique()} symbols.")
    log(f"Store now spans {merged['date'].min().date()} -> {merged['date'].max().date()}, "
        f"{merged['date'].nunique()} sessions, {merged['symbol'].nunique()} symbols.")
    log(f"({ok} pulled OK, {fail} failed, {covered} already covered.)")


if __name__ == "__main__":
    main()
