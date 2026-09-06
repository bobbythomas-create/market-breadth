#!/usr/bin/env python3
"""
Export the parquet price store to portable, gzipped CSV files, one per year.

Read-only on prices.parquet: it never modifies the store, only writes new
files under data/csv/. Safe to run anytime, any number of times.

Per-year files keep each one small (a few MB) and easy to fetch individually.
Any tool that reads CSV can read these; pandas/R/DuckDB read .csv.gz natively.
To open one in Excel, unzip it first (Windows/Mac both do this with a click).

Usage (no secret, no login needed):
  python scripts/export_csv.py
"""

import os
import pandas as pd

DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data")
STORE = os.path.join(DATA, "prices.parquet")
OUT = os.path.join(DATA, "csv")


def main():
    if not os.path.exists(STORE):
        print("No prices.parquet found. Run the backfill first.")
        return
    os.makedirs(OUT, exist_ok=True)

    df = pd.read_parquet(STORE)
    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values(["symbol", "date"])

    written = []
    for year, chunk in df.groupby(df["date"].dt.year):
        path = os.path.join(OUT, f"prices_{year}.csv.gz")
        chunk.to_csv(path, index=False, compression="gzip")
        mb = os.path.getsize(path) / 1e6
        written.append((year, len(chunk), round(mb, 1)))

    # A small manifest so the folder is self-describing.
    with open(os.path.join(OUT, "README.txt"), "w") as f:
        f.write("Portable daily OHLCV export of the market-breadth price store.\n")
        f.write("One gzipped CSV per year. Columns: date, symbol, open, high, "
                "low, close, prev_close, volume, turnover.\n")
        f.write("turnover is in rupees. Pre-2024 rows use a volume*close proxy; "
                "2024+ use exchange bhavcopy values.\n\n")
        f.write("year, rows, size_MB\n")
        for y, n, mb in written:
            f.write(f"{y}, {n}, {mb}\n")

    total = sum(n for _, n, _ in written)
    print(f"Wrote {len(written)} yearly files, {total} rows total, to data/csv/")
    for y, n, mb in written:
        print(f"  prices_{y}.csv.gz  {n} rows  {mb} MB")


if __name__ == "__main__":
    main()
