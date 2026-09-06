#!/usr/bin/env python3
"""
One-time Kite Connect historical backfill for the market-breadth price store.

Pulls daily OHLCV for the full NSE equity universe from 2019 up to the day
before the existing store begins, and folds it into data/prices.parquet in the
exact schema ingest.py expects: date, symbol, open, high, low, close,
prev_close, volume, turnover.

Runs on GitHub Actions (workflow_dispatch), NOT on the user's machine.

Env vars (set by the workflow):
  KITE_API_KEY        public app key
  KITE_API_SECRET     from GitHub encrypted secret
  KITE_REQUEST_TOKEN  pasted by the user when triggering the workflow
  BACKFILL_START      optional, default 2019-01-01
"""

import os, sys, time
from datetime import date, datetime, timedelta

import pandas as pd
from kiteconnect import KiteConnect

DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data")
STORE = os.path.join(DATA, "prices.parquet")
START = os.environ.get("BACKFILL_START", "2019-01-01")

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

    # 2. Decide the date window. Backfill only the gap BEFORE the existing store,
    #    so the free bhavcopy data (which has true turnover) stays authoritative.
    end = date.today()
    if os.path.exists(STORE):
        ex = pd.read_parquet(STORE, columns=["date"])
        if len(ex):
            existing_min = pd.to_datetime(ex["date"]).dt.date.min()
            end = existing_min - timedelta(days=1)
            log(f"Existing store starts {existing_min}. Backfilling up to {end}.")
    start = datetime.strptime(START, "%Y-%m-%d").date()
    if start >= end:
        log("Nothing to backfill: store already covers this range. Exiting clean.")
        return

    # 3. Instrument master -> NSE cash equities only.
    log("Fetching instrument master...")
    inst = pd.DataFrame(kite.instruments("NSE"))
    eq = inst[(inst["instrument_type"] == "EQ") & (inst["segment"] == "NSE")]
    eq = eq[["instrument_token", "tradingsymbol"]].drop_duplicates("tradingsymbol")
    log(f"{len(eq)} NSE equities to pull, {start} -> {end}.")

    # 4. Pull daily candles, throttled to stay under 3 req/sec, with retries.
    frames, ok, fail = [], 0, 0
    t_from = datetime.combine(start, datetime.min.time())
    t_to = datetime.combine(end, datetime.min.time())
    for i, (tok, sym) in enumerate(zip(eq["instrument_token"], eq["tradingsymbol"]), 1):
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
                    time.sleep(1.5 * (attempt + 1))  # backoff on rate limit
        time.sleep(0.34)  # ~2.9 req/sec
        if i % 250 == 0:
            log(f"  {i}/{len(eq)} done ({ok} ok, {fail} skipped)...")

    if not frames:
        log("No candles returned. Aborting without touching the store.")
        sys.exit(1)

    # 5. Shape to the store schema.
    new = pd.concat(frames, ignore_index=True)
    new["date"] = pd.to_datetime(new["date"]).dt.tz_localize(None).dt.normalize()
    new = new.sort_values(["symbol", "date"])
    # prev_close = previous session's close per symbol (clean ret downstream).
    new["prev_close"] = new.groupby("symbol")["close"].shift(1)
    # turnover proxy in rupees (bhavcopy stores rupees; liq filter divides by 1e7).
    new["turnover"] = new["volume"] * new["close"]
    new = new.dropna(subset=["prev_close"])
    new = new[STORE_COLS]

    # 6. Merge under the existing store; existing rows win on any overlap.
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
    log(f"({ok} symbols pulled OK, {fail} skipped.)")


if __name__ == "__main__":
    main()

