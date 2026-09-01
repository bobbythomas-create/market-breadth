#!/usr/bin/env python3
"""
Market breadth ingest.

Downloads NSE UDiFF bhavcopy (CM + FO), index constituent lists, maintains a
corporate-action-adjusted price store, and appends one breadth row per trading
day per universe.

Universes: ALL (NSE EQ), FNO, NIFTY50, NIFTYNEXT50, MIDCAP150, SMALLCAP250, NIFTY500

Outputs
  data/prices.parquet           raw + adjusted closes, all EQ series symbols
  data/fno_universe.parquet     point-in-time F&O underlying list per date
  data/indices.parquet          index closes per date
  data/constituents.json        current index constituent lists
  data/lists/YYYY-MM-DD.json    symbol names behind clickable count cells
  data/breadth_history.csv      ONE row per (date, universe) -- the only file the skill reads

Usage
  python ingest.py --backfill 2024-04-01
  python ingest.py                          # incremental
  python ingest.py --recompute              # rebuild breadth from the store
"""

import argparse, glob, io, json, os, sys, time, zipfile
from datetime import date, datetime, timedelta

import numpy as np
import pandas as pd
import requests

# ---------------------------------------------------------------- URLs

CM_URL = "https://nsearchives.nseindia.com/content/cm/BhavCopy_NSE_CM_0_0_0_{d}_F_0000.csv.zip"
FO_URL = "https://nsearchives.nseindia.com/content/fo/BhavCopy_NSE_FO_0_0_0_{d}_F_0000.csv.zip"
IDX_URL = "https://nsearchives.nseindia.com/content/indices/ind_close_all_{d2}.csv"
# ind_close_all carries Open/High/Low/Close per index, plus India VIX as an index row.
MIRROR_CM = "https://raw.githubusercontent.com/chartiny/nse-cm-bhavcopy/master/{y}/nse-cm-bhavcopy-{iso}.csv"

INDEX_CONSTITUENTS = {
    "NIFTY50":      "ind_nifty50list.csv",
    "NIFTYNEXT50":  "ind_niftynext50list.csv",
    "MIDCAP150":    "ind_niftymidcap150list.csv",
    "SMALLCAP250":  "ind_niftysmallcap250list.csv",
}

SECTOR_CONSTITUENTS = {
    "SEC_BANK":     "ind_niftybanklist.csv",
    "SEC_IT":       "ind_niftyitlist.csv",
    "SEC_PHARMA":   "ind_niftypharmalist.csv",
    "SEC_AUTO":     "ind_niftyautolist.csv",
    "SEC_FMCG":     "ind_niftyfmcglist.csv",
    "SEC_METAL":    "ind_niftymetallist.csv",
    "SEC_REALTY":   "ind_niftyrealtylist.csv",
    "SEC_ENERGY":   "ind_niftyenergylist.csv",
    "SEC_INFRA":    "ind_niftyinfralist.csv",
    "SEC_PSE":      "ind_niftypselist.csv",
    "SEC_FINSRV":   "ind_niftyfinancelist.csv",
    "SEC_MEDIA":    "ind_niftymedialist.csv",
}
CONST_URL = "https://nsearchives.nseindia.com/content/indices/{}"

INDEX_CLOSE_NAMES = {
    "nifty_close":      "Nifty 50",
    "niftynext50":      "Nifty Next 50",
    "midcap150_close":  "NIFTY MIDCAP 150",
    "smallcap250_close":"NIFTY SMLCAP 250",
    "nifty500_close":   "Nifty 500",
}

HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"),
    "Accept": "*/*", "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.nseindia.com/all-reports",
}

DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data")
EQ_SERIES = {"EQ"}
MA_WINDOWS = [10, 20, 40, 50, 200]   # 40 retained for T2108
UNIVERSES = (["ALL", "LIQUID", "FNO", "NIFTY50", "NIFTYNEXT50", "MIDCAP150", "SMALLCAP250", "NIFTY500"]
             + list(SECTOR_CONSTITUENTS.keys()))
EXTENDED_MULT = 1.15   # "extended" = adjusted close more than 15% above its 50 DMA
QUARTER = 65           # Bonde uses 65 sessions, not 63
# Liquidity filter, mirroring Bonde's own ($250k dollar volume plus a price floor).
LIQ_TURNOVER_CR = 2.0  # rupees crore of traded value
LIQ_PRICE = 20.0       # rupees


# ---------------------------------------------------------------- download

def _session():
    s = requests.Session()
    s.headers.update(HEADERS)
    try:
        s.get("https://www.nseindia.com/all-reports", timeout=15)
    except Exception:
        pass
    return s


def _get_zip_csv(sess, url, tries=2):
    for i in range(tries):
        try:
            r = sess.get(url, timeout=20)
            if r.status_code == 200 and r.content[:2] == b"PK":
                z = zipfile.ZipFile(io.BytesIO(r.content))
                return "ok", pd.read_csv(z.open(z.namelist()[0]), low_memory=False)
            if r.status_code == 404:
                return "missing", None
            print(f"  http {r.status_code}", file=sys.stderr)
        except Exception as e:
            print(f"  attempt {i+1}: {type(e).__name__}", file=sys.stderr)
        time.sleep(2)
    return "blocked", None


def fetch_cm(sess, d: date, prefer_mirror=False):
    status, df = ("skip", None) if prefer_mirror else _get_zip_csv(sess, CM_URL.format(d=d.strftime("%Y%m%d")))
    if df is None:
        try:
            u = MIRROR_CM.format(y=d.year, iso=d.isoformat())
            r = sess.get(u, timeout=30)
            if r.status_code == 200:
                df = pd.read_csv(io.StringIO(r.text), low_memory=False)
                print("  (mirror)", end=" ")
        except Exception:
            return status, None
    if df is None:
        return status, None
    df.columns = [c.strip() for c in df.columns]
    df = df[(df["FinInstrmTp"] == "STK") & (df["SctySrs"].astype(str).str.strip().isin(EQ_SERIES))]
    def num(col):
        return pd.to_numeric(df[col], errors="coerce") if col in df.columns else np.nan
    out = pd.DataFrame({
        "date": pd.to_datetime(d), "symbol": df["TckrSymb"].astype(str).str.strip(),
        "open": num("OpnPric"), "high": num("HghPric"), "low": num("LwPric"),
        "close": num("ClsPric"), "prev_close": num("PrvsClsgPric"),
        "volume": num("TtlTradgVol"), "turnover": num("TtlTrfVal"),
    })
    out = out.dropna(subset=["close", "prev_close"])
    out = out[(out["close"] > 0) & (out["prev_close"] > 0)]
    return "ok", out.drop_duplicates(subset=["symbol"])


def fetch_fno(sess, d: date):
    _, df = _get_zip_csv(sess, FO_URL.format(d=d.strftime("%Y%m%d")))
    if df is None:
        return None
    df.columns = [c.strip() for c in df.columns]
    stk = df[df["FinInstrmTp"].isin(["STF", "STO"])]
    syms = sorted(set(stk["TckrSymb"].astype(str).str.strip()))
    return pd.DataFrame({"date": pd.to_datetime(d), "symbol": syms})


# indices we want OHLC for, in index_ohlc.parquet. Name must match ind_close_all exactly.
OHLC_INDICES = {"NIFTY 50": "NIFTY50", "NIFTY BANK": "BANKNIFTY", "INDIA VIX": "INDIAVIX"}


def fetch_index(sess, d: date):
    """Returns (close_row_df, ohlc_df). close_row_df feeds breadth (per-segment close);
    ohlc_df carries O/H/L/C for Nifty, Bank Nifty and India VIX for the trader view."""
    try:
        r = sess.get(IDX_URL.format(d2=d.strftime("%d%m%Y")), timeout=20)
        if r.status_code != 200:
            return None, None
        df = pd.read_csv(io.StringIO(r.text))
        df.columns = [c.strip() for c in df.columns]
        name_col = [c for c in df.columns if "Index Name" in c][0]
        def col(frag):
            hit = [c for c in df.columns if frag.lower() in c.lower()]
            return hit[0] if hit else None
        cc, oc, hc, lc = col("Closing Index"), col("Open Index"), col("High Index"), col("Low Index")
        up = df[name_col].astype(str).str.strip().str.upper()

        row = {"date": pd.to_datetime(d)}
        for ckey, iname in INDEX_CLOSE_NAMES.items():
            m = df[up == iname.upper()]
            if not m.empty and cc:
                row[ckey] = float(m.iloc[0][cc])

        ohlc = []
        for iname, tag in OHLC_INDICES.items():
            m = df[up == iname.upper()]
            if m.empty:
                continue
            rec = {"date": pd.to_datetime(d), "index": tag}
            for k, c in [("open", oc), ("high", hc), ("low", lc), ("close", cc)]:
                if c:
                    try:
                        rec[k] = float(m.iloc[0][c])
                    except Exception:
                        rec[k] = np.nan
            ohlc.append(rec)
        cdf = pd.DataFrame([row]) if "nifty_close" in row else None
        odf = pd.DataFrame(ohlc) if ohlc else None
        return cdf, odf
    except Exception:
        return None, None


def fetch_constituents(sess):
    """Fetch current index + sector constituent lists from NSE. Returns dict or None."""
    result = {}
    for idx, filename in {**INDEX_CONSTITUENTS, **SECTOR_CONSTITUENTS}.items():
        try:
            r = sess.get(CONST_URL.format(filename), timeout=20)
            if r.status_code == 200:
                df = pd.read_csv(io.StringIO(r.text))
                df.columns = [c.strip() for c in df.columns]
                sym_col = [c for c in df.columns if "Symbol" in c][0]
                result[idx] = sorted(set(df[sym_col].astype(str).str.strip()))
                print(f"  constituents {idx}: {len(result[idx])}")
        except Exception as e:
            print(f"  constituents {idx}: failed ({e})", file=sys.stderr)
    core = [k for k in INDEX_CONSTITUENTS if k in result]
    if len(core) == len(INDEX_CONSTITUENTS):
        result["NIFTY500"] = sorted(set().union(*(set(result[k]) for k in core)))
        print(f"  constituents NIFTY500: {len(result['NIFTY500'])}")
    return result if result else None


def load_constituents():
    p = os.path.join(DATA, "constituents.json")
    if os.path.exists(p):
        return json.load(open(p))
    return None


def save_constituents(const):
    os.makedirs(DATA, exist_ok=True)
    const["updated"] = datetime.now().strftime("%Y-%m-%d")
    with open(os.path.join(DATA, "constituents.json"), "w") as f:
        json.dump(const, f, indent=2)


# ---------------------------------------------------------------- store

def _load(name, cols):
    p = os.path.join(DATA, name)
    if os.path.exists(p):
        return pd.read_parquet(p)
    return pd.DataFrame(columns=cols)


def _save(df, name):
    os.makedirs(DATA, exist_ok=True)
    df.to_parquet(os.path.join(DATA, name), index=False)


def update_store(start: date, end: date, max_days=0, prefer_mirror=False, refetch=False):
    prices = _load("prices.parquet", ["date", "symbol", "open", "high", "low", "close", "prev_close", "volume", "turnover"])
    fno = _load("fno_universe.parquet", ["date", "symbol"])
    idx = _load("indices.parquet", ["date", "nifty_close"])
    have = set() if refetch else (set(pd.to_datetime(prices["date"]).dt.date) if len(prices) else set())

    sess = _session()
    d, added, blocked_streak = start, 0, 0
    new_p, new_f, new_i, new_o = [], [], [], []
    while d <= end:
        if max_days and added >= max_days:
            print(f"reached --max-days {max_days}, stopping. Run again to continue.")
            break
        if d.weekday() < 5 and d not in have:
            print(f"{d} ...", end=" ", flush=True)
            status, cm = fetch_cm(sess, d, prefer_mirror)
            if status == "blocked":
                blocked_streak += 1
                print(f"BLOCKED ({blocked_streak}/5)")
                if blocked_streak >= 5:
                    print("\nNSE is not answering this machine. Aborting rather than crawling.\n"
                          "Options: rerun with --prefer-mirror for dates up to 2025-12-31, or run "
                          "this script on your own machine.", file=sys.stderr)
                    break
                d += timedelta(days=1)
                continue
            blocked_streak = 0
            if cm is None or len(cm) < 100:
                print("no data (holiday / not published)")
            else:
                new_p.append(cm)
                f = fetch_fno(sess, d)
                if f is not None:
                    new_f.append(f)
                i, o = fetch_index(sess, d)
                if i is not None:
                    new_i.append(i)
                if o is not None:
                    new_o.append(o)
                added += 1
                print(f"{len(cm)} EQ symbols" + ("" if f is None else f", {len(f)} F&O"))
            time.sleep(0.4)
        d += timedelta(days=1)

    # fetch / refresh constituents once per run
    const = fetch_constituents(sess)
    if const:
        save_constituents(const)
    else:
        const = load_constituents()
        if const:
            print("  using saved constituents from", const.get("updated", "unknown"))

    if new_p:
        prices = pd.concat([prices] + new_p, ignore_index=True)
        prices = prices.drop_duplicates(subset=["date", "symbol"], keep="last")
        _save(prices.sort_values(["symbol", "date"]), "prices.parquet")
    if new_f:
        fno = pd.concat([fno] + new_f, ignore_index=True).drop_duplicates(["date", "symbol"])
        _save(fno, "fno_universe.parquet")
    if new_i:
        idx = pd.concat([idx] + new_i, ignore_index=True).drop_duplicates(["date"], keep="last")
        _save(idx.sort_values("date"), "indices.parquet")
    if new_o:
        oh = _load("index_ohlc.parquet", ["date", "index", "open", "high", "low", "close"])
        oh = pd.concat([oh] + new_o, ignore_index=True).drop_duplicates(["date", "index"], keep="last")
        _save(oh.sort_values(["index", "date"]), "index_ohlc.parquet")
    print(f"store: {added} new sessions, {prices['date'].nunique()} total sessions")
    return prices, fno, idx, const


# ---------------------------------------------------------------- breadth

def adjusted_panel(prices):
    p = prices.sort_values(["symbol", "date"]).copy()
    p["ret"] = p["close"] / p["prev_close"] - 1.0
    p.loc[p["ret"].abs() > 0.85, "ret"] = np.nan
    p["ret"] = p["ret"].fillna(0.0)
    p["adj"] = p.groupby("symbol")["ret"].transform(lambda s: (1 + s).cumprod())
    return p


def write_lists(lists, keep_days=90):
    d = os.path.join(DATA, "lists")
    os.makedirs(d, exist_ok=True)
    for day, payload in lists.items():
        with open(os.path.join(d, f"{day}.json"), "w") as f:
            json.dump(payload, f, separators=(",", ":"))
    keep = sorted(glob.glob(os.path.join(d, "*.json")))[-keep_days:]
    for f in set(glob.glob(os.path.join(d, "*.json"))) - set(keep):
        os.remove(f)


def compute_breadth(prices, fno, idx, const) -> pd.DataFrame:
    p = adjusted_panel(prices)
    p["liq"] = (p["turnover"] / 1e7 > LIQ_TURNOVER_CR) & (p["close"] > LIQ_PRICE)
    adj = p.pivot(index="date", columns="symbol", values="adj").sort_index()
    ret = p.pivot(index="date", columns="symbol", values="ret").sort_index()
    traded = adj.notna()
    liqm = p.pivot(index="date", columns="symbol", values="liq").reindex(
        index=adj.index, columns=adj.columns).fillna(False).astype(bool)

    ma = {w: adj.rolling(w, min_periods=w).mean() for w in MA_WINDOWS}
    r21 = adj / adj.shift(21) - 1
    r5 = adj / adj.shift(5) - 1
    r65 = adj / adj.shift(QUARTER) - 1
    ext50 = adj > (ma[50] * EXTENDED_MULT)
    hi52 = adj.rolling(250, min_periods=100).max()
    lo52 = adj.rolling(250, min_periods=100).min()

    # F&O sets
    fno_sets = {}
    if len(fno):
        f = fno.copy(); f["date"] = pd.to_datetime(f["date"])
        fno_sets = {d: set(g["symbol"]) for d, g in f.groupby("date")}
    fno_dates = sorted(fno_sets)

    # constituent sets (single point-in-time for now)
    const_members = {}
    if const:
        for u in (["NIFTY50", "NIFTYNEXT50", "MIDCAP150", "SMALLCAP250", "NIFTY500"]
                  + list(SECTOR_CONSTITUENTS.keys())):
            if u in const:
                const_members[u] = set(const[u])

    rows, lists = [], {}
    for universe in UNIVERSES:
        if universe not in ("ALL", "LIQUID", "FNO") and universe not in const_members:
            continue
        for d in adj.index:
            live = traded.loc[d]
            if universe == "LIQUID":
                live = live & liqm.loc[d]
            elif universe == "FNO":
                if not fno_dates:
                    continue
                asof = max([x for x in fno_dates if x <= d], default=None)
                if asof is None:
                    continue
                live = live & adj.columns.isin(fno_sets[asof])
            elif universe in const_members:
                live = live & adj.columns.isin(const_members[universe])

            cols = adj.columns[live]
            n = len(cols)
            if n < 10:
                continue

            r = ret.loc[d, cols]
            row = {
                "date": d, "universe": universe, "universe_count": n,
                "advances": int((r > 0).sum()), "declines": int((r < 0).sum()),
                "unchanged": int((r == 0).sum()),
                "up_4pct": int((r >= 0.04).sum()), "down_4pct": int((r <= -0.04).sum()),
                "up_6pct": int((r >= 0.06).sum()), "down_6pct": int((r <= -0.06).sum()),
                "up_10pct": int((r >= 0.10).sum()), "down_10pct": int((r <= -0.10).sum()),
                "up_25pct_63d": int((r65.loc[d, cols] >= 0.25).sum()),
                "down_25pct_63d": int((r65.loc[d, cols] <= -0.25).sum()),
                "up_35pct_65d": int((r65.loc[d, cols] >= 0.35).sum()),
                "down_35pct_65d": int((r65.loc[d, cols] <= -0.35).sum()),
                "up_50pct_21d": int((r21.loc[d, cols] >= 0.50).sum()),
                "down_50pct_21d": int((r21.loc[d, cols] <= -0.50).sum()),
                "up_20pct_5d": int((r5.loc[d, cols] >= 0.20).sum()),
                "down_20pct_5d": int((r5.loc[d, cols] <= -0.20).sum()),
                "up_25pct_21d": int((r21.loc[d, cols] >= 0.25).sum()),
                "down_25pct_21d": int((r21.loc[d, cols] <= -0.25).sum()),
                "new_52w_high": int((adj.loc[d, cols] >= hi52.loc[d, cols] * 0.999).sum()),
                "new_52w_low": int((adj.loc[d, cols] <= lo52.loc[d, cols] * 1.001).sum()),
            }
            row["net_4pct"] = row["up_4pct"] - row["down_4pct"]
            row["net_6pct"] = row["up_6pct"] - row["down_6pct"]
            row["adv_ratio"] = round(row["advances"] / n, 4) if n else np.nan
            e = ext50.loc[d, cols]
            ev = ma[50].loc[d, cols].notna()
            row["extended_50dma"] = int((e & ev).sum())
            row["pct_extended_50dma"] = round(float((e[ev]).mean() * 100), 2) if ev.any() else np.nan
            h, l = row["new_52w_high"], row["new_52w_low"]
            row["hl_ratio"] = round(h / (h + l), 3) if (h + l) > 0 else np.nan

            key = d.strftime("%Y-%m-%d")
            lists.setdefault(key, {})[universe] = {
                "up4": sorted(cols[(r >= 0.04).values]),
                "dn4": sorted(cols[(r <= -0.04).values]),
                "hi52": sorted(cols[(adj.loc[d, cols] >= hi52.loc[d, cols] * 0.999).values]),
                "lo52": sorted(cols[(adj.loc[d, cols] <= lo52.loc[d, cols] * 1.001).values]),
                "up25": sorted(cols[(r21.loc[d, cols] >= 0.25).values]),
                "dn25": sorted(cols[(r21.loc[d, cols] <= -0.25).values]),
                "up10": sorted(cols[(r >= 0.10).values]),
                "dn10": sorted(cols[(r <= -0.10).values]),
                "up20_5d": sorted(cols[(r5.loc[d, cols] >= 0.20).values]),
                "dn20_5d": sorted(cols[(r5.loc[d, cols] <= -0.20).values]),
                "up25q": sorted(cols[(r65.loc[d, cols] >= 0.25).values]),
                "dn25q": sorted(cols[(r65.loc[d, cols] <= -0.25).values]),
                "ext50": sorted(cols[(ext50.loc[d, cols] & ma[50].loc[d, cols].notna()).values]),
                "up6": sorted(cols[(r >= 0.06).values]),
                "dn6": sorted(cols[(r <= -0.06).values]),
                "up35q": sorted(cols[(r65.loc[d, cols] >= 0.35).values]),
                "dn35q": sorted(cols[(r65.loc[d, cols] <= -0.35).values]),
                "up50m": sorted(cols[(r21.loc[d, cols] >= 0.50).values]),
            }

            for w in MA_WINDOWS:
                m = ma[w].loc[d, cols]; valid = m.notna()
                row[f"pct_above_{w}dma"] = round(float((adj.loc[d, cols][valid] > m[valid]).mean() * 100), 2) if valid.any() else np.nan
                row[f"cover_{w}dma"] = int(valid.sum())

            row["t2108"] = row.get("pct_above_40dma", np.nan)
            for a, b in [(10, 20), (20, 50), (50, 200)]:
                if a in MA_WINDOWS and b in MA_WINDOWS:
                    x, y = ma[a].loc[d, cols], ma[b].loc[d, cols]
                    v = x.notna() & y.notna()
                    row[f"pct_{a}dma_gt_{b}dma"] = round(float((x[v] > y[v]).mean() * 100), 2) if v.any() else np.nan
            rows.append(row)

    b = pd.DataFrame(rows).sort_values(["universe", "date"])
    if len(idx):
        i = idx.copy(); i["date"] = pd.to_datetime(i["date"])
        b = b.merge(i, on="date", how="left")
    else:
        b["nifty_close"] = np.nan
    b["nifty_chg_pct"] = b.groupby("universe")["nifty_close"].pct_change().mul(100).round(2)
    write_lists(lists)
    return add_divergence(b)


def add_divergence(b):
    """Price makes a 20d extreme, participation (% above 50 DMA) does not confirm."""
    out = []
    for u, g in b.groupby("universe"):
        g = g.sort_values("date").copy()
        px_hi = g["nifty_close"] >= g["nifty_close"].rolling(20, min_periods=20).max()
        px_lo = g["nifty_close"] <= g["nifty_close"].rolling(20, min_periods=20).min()
        br = g["pct_above_50dma"]
        br_hi = br >= br.rolling(20, min_periods=20).max()
        br_lo = br <= br.rolling(20, min_periods=20).min()
        g["div_bearish"] = (px_hi & ~br_hi).fillna(False)
        g["div_bullish"] = (px_lo & ~br_lo).fillna(False)
        g["net4_5d"] = g["net_4pct"].rolling(5, min_periods=5).sum()
        # Stockbee primary ratios. India-calibrated thresholds live in render.py.
        g["ratio_5d"] = (g["up_4pct"].rolling(5, min_periods=5).sum()
                         / g["down_4pct"].rolling(5, min_periods=5).sum().clip(lower=1)).round(2)
        g["ratio_10d"] = (g["up_4pct"].rolling(10, min_periods=10).sum()
                          / g["down_4pct"].rolling(10, min_periods=10).sum().clip(lower=1)).round(2)
        g["thrust"] = (g["up_4pct"] >= 0.10 * g["universe_count"]) & (g["up_4pct"] >= 3 * g["down_4pct"].clip(lower=1))
        # Zweig Breadth Thrust: 10-session advance ratio moves from below 0.40 to above
        # 0.615 within 10 sessions. Universe-size independent, and genuinely rare.
        ar10 = g["adv_ratio"].rolling(10, min_periods=10).mean()
        g["zweig_ar10"] = ar10.round(4)
        below = (ar10 < 0.40).rolling(10, min_periods=1).max().astype(bool)
        g["zweig"] = ((ar10 > 0.615) & below.shift(1).fillna(False)).fillna(False)
        out.append(g)
    return pd.concat(out, ignore_index=True)


# ---------------------------------------------------------------- validate

def validate(b):
    issues = []
    for u, g in b.groupby("universe"):
        g = g.sort_values("date")
        chk = g["advances"] + g["declines"] + g["unchanged"] - g["universe_count"]
        if (chk.abs() > 0).any():
            issues.append(f"{u}: adv+dec+unch != universe on {int((chk.abs()>0).sum())} day(s)")
        dup = g.duplicated(subset=["date"]).sum()
        if dup:
            issues.append(f"{u}: {dup} duplicate date rows")
        sig = g[["advances", "declines", "up_4pct", "nifty_close"]].astype(str).agg("|".join, axis=1)
        stale = (sig == sig.shift()).sum()
        if stale:
            issues.append(f"{u}: {stale} row(s) identical to previous session (stale copy)")
        drift = g["universe_count"].pct_change().abs()
        if (drift > 0.10).any():
            issues.append(f"{u}: universe size jumped >10% on {int((drift>0.10).sum())} day(s)")
        if g["nifty_close"].isna().any():
            issues.append(f"{u}: Nifty close missing on {int(g['nifty_close'].isna().sum())} day(s)")
    return issues


# ---------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--backfill", help="start date YYYY-MM-DD")
    ap.add_argument("--end", help="end date YYYY-MM-DD (default today)")
    ap.add_argument("--recompute", action="store_true")
    ap.add_argument("--max-days", type=int, default=120)
    ap.add_argument("--prefer-mirror", action="store_true")
    ap.add_argument("--refetch", action="store_true", help="re-fetch and overwrite existing rows (use once to add OHLC to history)")
    a = ap.parse_args()

    end = datetime.strptime(a.end, "%Y-%m-%d").date() if a.end else date.today()

    if a.recompute:
        prices = _load("prices.parquet", [])
        fno = _load("fno_universe.parquet", [])
        idx = _load("indices.parquet", [])
        const = load_constituents()
    else:
        if a.backfill:
            start = datetime.strptime(a.backfill, "%Y-%m-%d").date()
        else:
            ex = _load("prices.parquet", ["date"])
            start = (pd.to_datetime(ex["date"]).max().date() + timedelta(days=1)) if len(ex) else end - timedelta(days=400)
        prices, fno, idx, const = update_store(start, end, a.max_days, a.prefer_mirror, a.refetch)

    if not len(prices):
        sys.exit("no price data in store")

    b = compute_breadth(prices, fno, idx, const)
    os.makedirs(DATA, exist_ok=True)
    out = os.path.join(DATA, "breadth_history.csv")
    b.to_csv(out, index=False)

    issues = validate(b)
    with open(os.path.join(DATA, "validation.txt"), "w") as f:
        f.write(f"generated: {datetime.now().isoformat(timespec='seconds')}\n")
        f.write(f"sessions: {b['date'].nunique()}  rows: {len(b)}\n")
        f.write("\n".join(issues) if issues else "no issues found")
    print(f"wrote {out}: {len(b)} rows, {b['date'].nunique()} sessions, {b['universe'].nunique()} universes")
    print("validation:", "; ".join(issues) if issues else "clean")


if __name__ == "__main__":
    main()
