#!/usr/bin/env python3
"""
One-time Kite Connect historical backfill for the market-breadth price store.

Pulls daily OHLCV from BACKFILL_START up to the day before each symbol's
existing coverage, and folds it into data/prices.parquet in the schema
ingest.py expects: date, symbol, open, high, low, close, prev_close,
volume, turnover.

Runs on GitHub Actions (workflow_dispatch), NOT on the user's machine.

Env vars (set by the workflow):
  KITE_API_KEY        from GitHub encrypted secret
  KITE_API_SECRET     from GitHub encrypted secret
  KITE_REQUEST_TOKEN  pasted by the user when triggering the workflow
  BACKFILL_START      optional, default 2019-01-01
  BACKFILL_UNIVERSE   'store' (default) = only symbols already in the store (~2k, fast)
                      'all'             = every NSE EQ symbol (~18k, ~3h, memory-heavy)
"""

import os, sys, time
from datetime import date, datetime, timedelta

import pandas as pd
from kiteconnect import KiteConnect

DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data")
STORE = os.path.join(DATA, "prices.parquet")
START = os.environ.get("BACKFILL_START", "2019-01-01")
UNIVERSE = os.environ.get("BACKFILL_UNIVERSE", "store").strip().lower()

API_KEY = os.environ["KITE_API_KEY"]
API_SECRET = os.environ["KITE_API_SECRET"]
REQUEST_TOKEN = os.environ["KITE_REQUEST_TOKEN"].strip()

STORE_COLS = ["date", "symbol", "open", "high", "low", "close",
              "prev_close", "volume", "turnover"]


def log(m):
    print(m, flush=True)


def main():
    # 1. Exchange the request_token for a same-day access_token.
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

    # 2. Read what the store already covers, per symbol. Each symbol is later
    #    pulled only for the gap BEFORE its own data begins, so the free bhavcopy
    #    rows (with true turnover) stay authoritative on any overlap.
    existing_symbols = set()
    sym_min = {}
    if os.path.exists(STORE):
        ex = pd.read_parquet(STORE, columns=["date", "symbol"])
        if len(ex):
            ex["date"] = pd.to_datetime(ex["date"]).dt.date
            existing_symbols = set(ex["symbol"].unique())
            sym_min = ex.groupby("symbol")["date"].min().to_dict()
            log(f"Store holds {len(existing_symbols)} symbols, "
                f"earliest date {min(sym_min.values())}.")

    # 3. Instrument master -> NSE cash equities.
    log("Fetching instrument master...")
    inst = pd.DataFrame(kite.instruments("NSE"))
    eq = inst[(inst["instrument_type"] == "EQ") & (inst["segment"] == "NSE")]
    eq = eq[["instrument_token", "tradingsymbol"]].drop_duplicates("tradingsymbol")
    full_n = len(eq)

    # 4. Trim the universe to the store unless the user explicitly asked for all.
    if UNIVERSE == "store":
        if not existing_symbols:
            log("universe=store but the store is empty, nothing to trim against.")
            log("Seed the store with a nightly ingest first, or re-run with universe=all.")
            sys.exit(1)
        eq = eq[eq["tradingsymbol"].isin(existing_symbols)]
        log(f"universe=store: {full_n} NSE EQ trimmed to {len(eq)} in-store symbols.")
    else:
        log(f"universe=all: pulling all {full_n} NSE EQ symbols. Expect ~3+ hours.")

    if not len(eq):
        log("No symbols to pull after filtering. Exiting clean.")
        return

    # 5. Pull daily candles per symbol, throttled under 3 req/sec, with retries.
    frames, ok, fail, covered = [], 0, 0, 0
    total = len(eq)
    for i, (tok, sym) in enumerate(zip(eq["instrument_token"], eq["tradingsymbol"]), 1):
        s_min = sym_min.get(sym)
        d_to = (s_min - timedelta(days=1)) if s_min else today
        if start >= d_to:                 # already covered from START, skip
            covered += 1
            if i % 250 == 0:
                log(f"  {i}/{total} done ({ok} ok, {fail} skipped, {covered} already covered)...")
            continue
        t_from = datetime.combine(start, datetime.min.time())
        t_to = datetime.combine(d_to, datetime.min.time())
        for attempt in range(4):
            try:
                candles = kite.historical_data(int(tok), t_from, t_to, "day")
                if candles:
                    df = pd.DataFrame(candles)
                    df["symbol"] = sym
                    frames.append(df[["date", "symbol", "open", "high",
                                      "low", "close", "volume"]])
                ok += 1
                break
            except Exception as e:
                if attempt == 3:
                    fail += 1
                    log(f"  skip {sym}: {e}")
                else:
                    time.sleep(1.5 * (attempt + 1))   # backoff on rate limit
        time.sleep(0.34)                              # ~2.9 req/sec
        if i % 250 == 0:
            log(f"  {i}/{total} done ({ok} ok, {fail} skipped, {covered} already covered)...")

    if not frames:
        log("No new candles pulled (store may already cover this range). "
            "Exiting without touching the store.")
        return

    # 6. Shape to the store schema.
    new = pd.concat(frames, ignore_index=True)
    new["date"] = pd.to_datetime(new["date"]).dt.tz_localize(None).dt.normalize()
    new = new.sort_values(["symbol", "date"])
    new["prev_close"] = new.groupby("symbol")["close"].shift(1)
    new["turnover"] = new["volume"] * new["close"]
    new = new.dropna(subset=["prev_close"])
    new = new[STORE_COLS]

    # 7. Merge under the existing store; existing rows win on any overlap.
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
    log(f"({ok} pulled OK, {fail} skipped, {covered} already covered.)")


if __name__ == "__main__":
    main()
