#!/usr/bin/env python3
"""
Per-stock screen, run AFTER ingest.py each session.

Computes, for every NSE EQ stock with enough history:
  - IBD-style RS rank (weighted 3/6/12-month return), as a 1-99 percentile against
    three benchmark pools: whole universe, own sector, Nifty 50.
  - Minervini 8-point Trend Template (strict) and a relaxed variant.
  - Stage tag (Weinstein 1-4) from the 30-week / 150-day structure.

Reads : data/prices.parquet, data/constituents.json
Writes: data/stocks.json   (compact, the only file the dashboard reads for this)

The dashboard never reads the price store; it reads stocks.json. Keep this file small:
one record per qualifying stock, plus the full RS table trimmed to what the UI needs.

Usage
  python screen.py
  python screen.py --min-rs 70 --near-high 25
"""

import argparse, io, json, os, time
import numpy as np
import pandas as pd
import requests

DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data")

CONST_URL = "https://nsearchives.nseindia.com/content/indices/{}"
CONST_FILES = {
    "NIFTY50": "ind_nifty50list.csv", "NIFTYNEXT50": "ind_niftynext50list.csv",
    "MIDCAP150": "ind_niftymidcap150list.csv", "SMALLCAP250": "ind_niftysmallcap250list.csv",
    "SEC_BANK": "ind_niftybanklist.csv", "SEC_IT": "ind_niftyitlist.csv",
    "SEC_PHARMA": "ind_niftypharmalist.csv", "SEC_AUTO": "ind_niftyautolist.csv",
    "SEC_FMCG": "ind_niftyfmcglist.csv", "SEC_METAL": "ind_niftymetallist.csv",
    "SEC_REALTY": "ind_niftyrealtylist.csv", "SEC_ENERGY": "ind_niftyenergylist.csv",
    "SEC_INFRA": "ind_niftyinfralist.csv", "SEC_PSE": "ind_niftypselist.csv",
    "SEC_FINSRV": "ind_niftyfinancelist.csv", "SEC_MEDIA": "ind_niftymedialist.csv",
}
HEADERS = {"User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
           "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"), "Referer": "https://www.nseindia.com/"}


def fetch_constituents_live():
    """Fetch sector + index constituents from NSE. Merge over the saved file so a
    partial NSE outage never wipes sector membership the screen depends on."""
    saved = {}
    cpath = os.path.join(DATA, "constituents.json")
    if os.path.exists(cpath):
        saved = json.load(open(cpath))
    sess = requests.Session(); sess.headers.update(HEADERS)
    try:
        sess.get("https://www.nseindia.com/", timeout=15)
    except Exception:
        pass
    got = dict(saved)
    n = 0
    for key, fn in CONST_FILES.items():
        try:
            r = sess.get(CONST_URL.format(fn), timeout=20)
            if r.status_code == 200:
                df = pd.read_csv(io.StringIO(r.text)); df.columns = [c.strip() for c in df.columns]
                col = [c for c in df.columns if "Symbol" in c][0]
                got[key] = sorted(set(df[col].astype(str).str.strip()))
                n += 1
            time.sleep(0.3)
        except Exception:
            pass
    if n:
        print(f"  refreshed {n} constituent lists from NSE")
        # persist the enriched file so ingest and screen stay in sync
        try:
            json.dump(got, open(cpath, "w"))
        except Exception:
            pass
    else:
        print("  NSE constituents unreachable, using saved file")
    return got

SECTOR_KEYS = ["SEC_BANK", "SEC_FINSRV", "SEC_IT", "SEC_PHARMA", "SEC_AUTO", "SEC_FMCG",
               "SEC_METAL", "SEC_ENERGY", "SEC_INFRA", "SEC_REALTY", "SEC_PSE", "SEC_MEDIA"]
SECTOR_LABEL = {"SEC_BANK": "Bank", "SEC_FINSRV": "Fin Svcs", "SEC_IT": "IT", "SEC_PHARMA": "Pharma",
                "SEC_AUTO": "Auto", "SEC_FMCG": "FMCG", "SEC_METAL": "Metal", "SEC_ENERGY": "Energy",
                "SEC_INFRA": "Infra", "SEC_REALTY": "Realty", "SEC_PSE": "PSE", "SEC_MEDIA": "Media"}

# IBD weighted RS: most recent quarter double-weighted.
# periods in trading sessions; weights sum to 1.
RS_PERIODS = [(63, 0.40), (126, 0.20), (189, 0.20), (252, 0.20)]


def adjusted_panel(prices):
    p = prices.sort_values(["symbol", "date"]).copy()
    p["ret"] = p["close"] / p["prev_close"] - 1.0
    p.loc[p["ret"].abs() > 0.85, "ret"] = np.nan
    p["ret"] = p["ret"].fillna(0.0)
    p["adj"] = p.groupby("symbol")["ret"].transform(lambda s: (1 + s).cumprod())
    return p


def weighted_rs_raw(adj):
    """Weighted multi-period return per symbol on the last row. Returns a Series."""
    last = adj.iloc[-1]
    score = pd.Series(0.0, index=adj.columns)
    wsum = 0.0
    for n, w in RS_PERIODS:
        if len(adj) > n:
            past = adj.iloc[-1 - n]
            r = last / past - 1.0
            score = score.add(r * w, fill_value=np.nan)
            wsum += w
    if wsum > 0:
        score = score / wsum
    return score


def pct_rank(series, pool_mask=None):
    """1-99 percentile rank within the (optionally masked) pool."""
    s = series.copy()
    if pool_mask is not None:
        s = s[pool_mask]
    valid = s.dropna()
    if len(valid) < 5:
        return pd.Series(np.nan, index=series.index)
    ranks = valid.rank(pct=True) * 98 + 1
    return ranks.reindex(series.index)


def stage_tag(price, ma30w, ma30w_prev):
    """Weinstein stage from 30-week (150-day) line. Coarse."""
    if np.isnan(ma30w) or np.isnan(price):
        return "?"
    rising = not np.isnan(ma30w_prev) and ma30w > ma30w_prev
    if price > ma30w and rising:
        return "2"          # advancing
    if price > ma30w and not rising:
        return "1"          # basing
    if price < ma30w and rising:
        return "3"          # topping
    return "4"              # declining


def build(min_rs, near_high):
    prices = pd.read_parquet(os.path.join(DATA, "prices.parquet"))
    const = fetch_constituents_live()

    p = adjusted_panel(prices)
    adj = p.pivot(index="date", columns="symbol", values="adj").sort_index()
    close = p.pivot(index="date", columns="symbol", values="close").sort_index()
    asof = adj.index[-1]

    ma = {w: adj.rolling(w, min_periods=w).mean() for w in (50, 150, 200)}
    ma30w = ma[150]                       # 150 trading days approx 30 weeks
    hi52 = adj.rolling(252, min_periods=200).max()
    lo52 = adj.rolling(252, min_periods=200).min()
    ma200_prev = ma[200].shift(21)
    ma30w_prev = ma30w.shift(21)

    last = adj.iloc[-1]
    lastc = close.iloc[-1]

    # RS raw then three ranked pools
    rs_raw = weighted_rs_raw(adj)
    sym_to_sector = {}
    for k in SECTOR_KEYS:
        for sym in const.get(k, []):
            sym_to_sector[sym] = k
    n50 = set(const.get("NIFTY50", []))

    rs_univ = pct_rank(rs_raw)
    rs_n50 = pct_rank(rs_raw, rs_raw.index.isin(n50))
    # sector rank: rank within each sector pool, stitch together
    rs_sec = pd.Series(np.nan, index=rs_raw.index)
    for k in SECTOR_KEYS:
        members = [s for s in const.get(k, []) if s in rs_raw.index]
        if len(members) >= 5:
            r = pct_rank(rs_raw, rs_raw.index.isin(members))
            rs_sec.loc[members] = r.loc[members]

    records = []
    for sym in adj.columns:
        pr = last[sym]
        c = lastc[sym]
        if np.isnan(pr):
            continue
        m50, m150, m200 = ma[50].iloc[-1][sym], ma[150].iloc[-1][sym], ma[200].iloc[-1][sym]
        if np.isnan(m200):          # needs a year of history
            continue
        h, l = hi52.iloc[-1][sym], lo52.iloc[-1][sym]
        m200p = ma200_prev.iloc[-1][sym]

        pct_from_high = (pr / h - 1) * 100 if h else np.nan       # negative = below high
        pct_from_low = (pr / l - 1) * 100 if l else np.nan
        m200_rising = (not np.isnan(m200p)) and m200 > m200p

        # Minervini 8 (strict)
        c1 = pr > m150 and pr > m200
        c2 = m150 > m200
        c3 = m200_rising
        c4 = m50 > m150 and m50 > m200
        c5 = pr > m50
        c6 = pct_from_low >= 30
        c7 = pct_from_high >= -25          # within 25% of high
        rsu = rs_univ.get(sym, np.nan)
        c8 = (not np.isnan(rsu)) and rsu >= min_rs
        strict = all([c1, c2, c3, c4, c5, c6, c7, c8])
        # relaxed drops the within-25%-of-high rule and softens RS to 60
        relaxed = all([c1, c2, c3, c4, c5, c6]) and (np.isnan(rsu) or rsu >= 60)
        pass_ct = sum([c1, c2, c3, c4, c5, c6, c7, c8])

        if not (strict or relaxed):
            continue

        records.append({
            "s": sym,
            "sec": SECTOR_LABEL.get(sym_to_sector.get(sym, ""), ""),
            "rsu": None if np.isnan(rsu) else int(round(rsu)),
            "rss": None if np.isnan(rs_sec.get(sym, np.nan)) else int(round(rs_sec.get(sym))),
            "rsn": None if np.isnan(rs_n50.get(sym, np.nan)) else int(round(rs_n50.get(sym))),
            "fh": None if np.isnan(pct_from_high) else round(pct_from_high, 1),
            "fl": None if np.isnan(pct_from_low) else round(pct_from_low, 0),
            "px": round(float(c), 1),
            "stg": stage_tag(pr, m150, ma30w_prev.iloc[-1][sym]),
            "strict": bool(strict),
            "pc": int(pass_ct),
        })

    # sort: strict first, then RS
    records.sort(key=lambda r: (not r["strict"], -(r["rsu"] or 0)))

    out = {
        "asof": pd.Timestamp(asof).strftime("%Y-%m-%d"),
        "min_rs": min_rs, "near_high": near_high,
        "universe": int(rs_raw.notna().sum()),
        "n_strict": sum(1 for r in records if r["strict"]),
        "n_relaxed": sum(1 for r in records if not r["strict"]),
        "stocks": records[:400],
        "sectors": SECTOR_LABEL,
    }
    with open(os.path.join(DATA, "stocks.json"), "w") as f:
        json.dump(out, f, separators=(",", ":"))

    print(f"screen -> data/stocks.json  asof {out['asof']}")
    print(f"  universe with RS: {out['universe']}  strict: {out['n_strict']}  relaxed extra: {out['n_relaxed']}")
    top = [r["s"] for r in records if r["strict"]][:12]
    print("  strict leaders:", ", ".join(top) if top else "none")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--min-rs", type=int, default=70)
    ap.add_argument("--near-high", type=int, default=25)
    a = ap.parse_args()
    build(a.min_rs, a.near_high)
