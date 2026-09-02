#!/usr/bin/env python3
"""
Render breadth_history.csv into a self-contained dark-theme HTML dashboard.

Tabs: Table | Charts | Sectors | Compare | Regime | Scanner | Guide

Token discipline: this script prints ONE summary line per universe. Never print
or read the CSV into a model's context.
"""

import argparse, glob, json, os
import numpy as np, pandas as pd

# ---------------------------------------------------------------- columns
# Trend / structure / extremes / price lead. Momentum and range follow.
GROUPS = [
    ("Trend, % above DMA", [("pct_above_10dma", "10", None), ("pct_above_20dma", "20", None),
                            ("t2108", "T2108", None), ("pct_above_50dma", "50", None),
                            ("pct_above_200dma", "200", None), ("pct_extended_50dma", "Ext", "ext50")]),
    ("MA structure", [("pct_10dma_gt_20dma", "10>20", None), ("pct_20dma_gt_50dma", "20>50", None),
                      ("pct_50dma_gt_200dma", "50>200", None)]),
    ("Extremes", [("new_52w_high", "52wH", "hi52"), ("new_52w_low", "52wL", "lo52"),
                  ("hl_ratio", "H/L", None)]),
    ("Nifty", [("nifty_close", "Close", None), ("nifty_chg_pct", "Chg%", None)]),
    ("Breadth", [("advances", "Adv", None), ("declines", "Dec", None)]),
    ("Daily momentum", [("up_4pct", "U4", "up4"), ("down_4pct", "D4", "dn4"),
                        ("net_4pct", "Net4", None), ("up_6pct", "U6", "up6"), ("down_6pct", "D6", "dn6"),
                        ("up_10pct", "U10", "up10"), ("down_10pct", "D10", "dn10"),
                        ("ratio_5d", "R5", None), ("ratio_10d", "R10", None)]),
    ("Range movers", [("up_25pct_21d", "U25m", "up25"), ("down_25pct_21d", "D25m", "dn25"),
                      ("up_50pct_21d", "U50m", "up50m"),
                      ("up_25pct_63d", "U25q", "up25q"), ("down_25pct_63d", "D25q", "dn25q"),
                      ("up_35pct_65d", "U35q", "up35q"), ("down_35pct_65d", "D35q", "dn35q"),
                      ("up_20pct_5d", "U20w", "up20_5d"), ("down_20pct_5d", "D20w", "dn20_5d")]),
]
COLS = [(k, l, lk) for _, cs in GROUPS for k, l, lk in cs]
PCT_FMT = {k for k, _, _ in COLS if k.startswith("pct_")} | {"t2108"}
NARROW = {"up_4pct", "down_4pct", "net_4pct", "up_6pct", "down_6pct", "up_10pct", "down_10pct",
          "ratio_5d", "ratio_10d", "up_25pct_21d", "down_25pct_21d", "up_50pct_21d",
          "up_25pct_63d", "down_25pct_63d", "up_35pct_65d", "down_35pct_65d",
          "up_20pct_5d", "down_20pct_5d", "advances", "declines"}

# ---------------------------------------------------------------- colour anchors
DMA_ANCHORS = [(0, 0.0), (15, 0.12), (30, 0.28), (42, 0.42), (50, 0.50),
               (58, 0.58), (70, 0.72), (85, 0.88), (100, 1.0)]

# India-calibrated. Bonde's US thresholds (2.0 / 0.5) do NOT transfer: the Indian
# 5-day ratio has a median near 1.7 because the EQ universe carries thousands of
# thin microcaps that clear 4% on trivial volume.
RATIO_ANCHORS_5 = [(0.3, 0.02), (0.5, 0.10), (0.9, 0.28), (1.7, 0.50),
                   (3.1, 0.70), (5.0, 0.88), (9.0, 1.0)]
RATIO_ANCHORS_10 = [(0.4, 0.02), (0.7, 0.10), (1.0, 0.28), (1.65, 0.50),
                    (2.5, 0.70), (3.5, 0.88), (6.0, 1.0)]

COUNT_PROFILES = {
    "WIDE": {   # ALL, NIFTY500, SMALLCAP250
        "advances": [(20, 0.05), (35, 0.2), (45, 0.4), (50, 0.5), (55, 0.6), (65, 0.8), (80, 0.95)],
        "up_4pct": [(0.5, 0.1), (2, 0.25), (3.5, 0.4), (5, 0.55), (7, 0.7), (10, 0.85), (15, 0.95)],
        "up_10pct": [(0.05, 0.1), (0.2, 0.25), (0.5, 0.42), (0.9, 0.55), (1.5, 0.72), (2.5, 0.88), (4, 0.97)],
        "net_4pct": [(-10, 0.05), (-5, 0.15), (-2, 0.35), (0, 0.5), (2, 0.65), (5, 0.85), (10, 0.95)],
        "up_25pct_21d": [(0.2, 0.1), (0.6, 0.2), (1.5, 0.4), (3, 0.55), (5, 0.7), (8, 0.85), (12, 0.95)],
        "up_25pct_63d": [(0.5, 0.08), (1.5, 0.2), (3, 0.38), (6, 0.55), (10, 0.72), (16, 0.88), (24, 0.97)],
        "up_20pct_5d": [(0.05, 0.1), (0.15, 0.2), (0.3, 0.4), (0.6, 0.55), (1, 0.7), (1.8, 0.85), (3, 0.95)],
        "new_52w_high": [(0.2, 0.1), (0.8, 0.2), (1.5, 0.35), (3, 0.5), (5, 0.65), (8, 0.8), (12, 0.95)],
    },
    "NARROW": {  # FNO, NIFTY50, NEXT50, MIDCAP150, sectors
        "advances": [(20, 0.05), (35, 0.2), (45, 0.4), (50, 0.5), (55, 0.6), (65, 0.8), (80, 0.95)],
        "up_4pct": [(0.3, 0.15), (0.8, 0.3), (1.5, 0.45), (2.5, 0.55), (4, 0.7), (6, 0.85), (10, 0.95)],
        "up_10pct": [(0.05, 0.15), (0.15, 0.3), (0.3, 0.45), (0.6, 0.58), (1, 0.75), (1.8, 0.9), (3, 0.98)],
        "net_4pct": [(-7, 0.05), (-3.5, 0.15), (-1, 0.35), (0, 0.5), (1, 0.65), (3.5, 0.85), (7, 0.95)],
        "up_25pct_21d": [(0.1, 0.1), (0.3, 0.2), (0.6, 0.4), (1.2, 0.55), (2, 0.7), (4, 0.85), (7, 0.95)],
        "up_25pct_63d": [(0.3, 0.08), (0.8, 0.2), (1.8, 0.38), (3.5, 0.55), (6, 0.72), (10, 0.88), (16, 0.97)],
        "up_20pct_5d": [(0.05, 0.15), (0.1, 0.3), (0.2, 0.45), (0.4, 0.55), (0.7, 0.7), (1, 0.85), (2, 0.95)],
        "new_52w_high": [(0.3, 0.1), (1, 0.2), (2, 0.35), (3.5, 0.5), (5.5, 0.65), (8, 0.8), (13, 0.95)],
    },
}
for prof in COUNT_PROFILES.values():
    prof["declines"] = prof["advances"]
    prof["down_4pct"] = prof["up_4pct"]
    prof["down_10pct"] = prof["up_10pct"]
    prof["down_25pct_21d"] = prof["up_25pct_21d"]
    prof["down_25pct_63d"] = prof["up_25pct_63d"]
    prof["down_20pct_5d"] = prof["up_20pct_5d"]
    prof["new_52w_low"] = prof["new_52w_high"]

INVERTED = {"declines", "down_4pct", "down_6pct", "down_10pct", "down_25pct_21d", "down_25pct_63d",
            "down_35pct_65d", "down_20pct_5d", "new_52w_low"}

SECTORS = ["SEC_BANK", "SEC_FINSRV", "SEC_IT", "SEC_PHARMA", "SEC_AUTO", "SEC_FMCG",
           "SEC_METAL", "SEC_ENERGY", "SEC_INFRA", "SEC_REALTY", "SEC_PSE", "SEC_MEDIA"]
SIZE_UNIVERSES = ["ALL", "LIQUID", "FNO", "NIFTY50", "NIFTYNEXT50", "MIDCAP150", "SMALLCAP250", "NIFTY500"]
ULBL = {"ALL": "All NSE", "LIQUID": "Liquid", "FNO": "F&O", "NIFTY50": "Nifty 50", "NIFTYNEXT50": "Next 50",
        "MIDCAP150": "Midcap 150", "SMALLCAP250": "Smallcap 250", "NIFTY500": "Nifty 500",
        "SEC_BANK": "Bank", "SEC_FINSRV": "Fin Services", "SEC_IT": "IT", "SEC_PHARMA": "Pharma",
        "SEC_AUTO": "Auto", "SEC_FMCG": "FMCG", "SEC_METAL": "Metal", "SEC_ENERGY": "Energy",
        "SEC_INFRA": "Infra", "SEC_REALTY": "Realty", "SEC_PSE": "PSE", "SEC_MEDIA": "Media"}
WIDE_SET = {"ALL", "LIQUID", "NIFTY500", "SMALLCAP250"}


def _interp(anchors, v):
    return float(np.clip(np.interp(v, [a[0] for a in anchors], [a[1] for a in anchors]), 0, 1))


def shade(universe, key, value, n):
    if value is None or (isinstance(value, float) and np.isnan(value)):
        return None
    if key == "nifty_close":
        return None
    if key.startswith("pct_") or key == "t2108":
        if key == "pct_extended_50dma":      # froth gauge, not a bull gauge
            return round(_interp([(0, .5), (8, .62), (18, .75), (30, .5), (42, .28), (55, .1)], value), 2)
        return round(_interp(DMA_ANCHORS, value), 2)
    if key == "hl_ratio":
        return round(_interp([(0, 0), (.2, .12), (.4, .35), (.5, .5), (.65, .68), (.85, .9), (1, 1)], value), 2)
    if key == "ratio_5d":
        return round(_interp(RATIO_ANCHORS_5, value), 2)
    if key == "ratio_10d":
        return round(_interp(RATIO_ANCHORS_10, value), 2)
    if key == "nifty_chg_pct":
        return round(_interp([(-3, 0), (-1.5, .1), (-.5, .35), (0, .5), (.5, .65), (1.5, .9), (3, 1)], value), 2)
    prof = COUNT_PROFILES["WIDE" if universe in WIDE_SET else "NARROW"]
    ALIAS = {"up_6pct": "up_4pct", "down_6pct": "down_4pct",
             "up_35pct_65d": "up_25pct_63d", "down_35pct_65d": "down_25pct_63d",
             "up_50pct_21d": "up_25pct_21d"}
    anchors = prof.get(key) or prof.get(ALIAS.get(key, ""))
    if not anchors or not n:
        return None
    s = _interp(anchors, value / n * 100)
    return round(1.0 - s if key in INVERTED else s, 2)


# Action-oriented regime, Bonde style. Primary gauge is % above 50 DMA; the 5-day
# ratio and the 25%-in-quarter direction refine the call. Labels tell you what to DO.
REGIME_ORDER = ["Aggressive", "Normal", "Defensive", "Stand aside", "Recovery watch", "n/a"]
REGIME_TONE = {"Aggressive": 0.90, "Normal": 0.60, "Defensive": 0.28,
               "Stand aside": 0.08, "Recovery watch": 0.42, "n/a": 0.5}
REGIME_SIZE = {"Aggressive": "Full size, buy breakouts freely",
               "Normal": "Standard size, be selective",
               "Defensive": "Half size, tighten stops, favour leaders only",
               "Stand aside": "No new longs, protect capital",
               "Recovery watch": "Washed-out and turning up: scale in as thrust confirms",
               "n/a": "Insufficient history"}


def regime(row):
    a50 = row.get("pct_above_50dma", np.nan)
    if pd.isna(a50):
        return "n/a"
    r5 = row.get("ratio_5d", np.nan)
    if a50 < 12:
        # deep washout. If the 5-day ratio has turned up hard, it is a recovery setup.
        return "Recovery watch" if (not pd.isna(r5) and r5 >= 1.5) else "Stand aside"
    if a50 >= 58 and (pd.isna(r5) or r5 >= 1.0):
        return "Aggressive"
    if a50 >= 45:
        return "Normal"
    if a50 >= 25:
        return "Defensive"
    return "Stand aside"


def observations(g, universe):
    obs = []
    if len(g) < 12:
        return obs
    last = g.iloc[-1]
    reg = regime(last)
    obs.append(f"Regime: {reg} \u2192 {REGIME_SIZE.get(reg, '')}")
    a50 = g["pct_above_50dma"].values
    a10 = g["pct_above_10dma"].values
    a200 = g["pct_above_200dma"].values

    d5 = a50[-5:]
    if len(d5) == 5 and not np.isnan(d5).any():
        if all(d5[i] >= d5[i - 1] for i in range(1, 5)):
            obs.append(f"50 DMA breadth: rising 5 sessions, {d5[0]:.0f}\u2192{d5[-1]:.0f}")
        elif all(d5[i] <= d5[i - 1] for i in range(1, 5)):
            obs.append(f"50 DMA breadth: falling 5 sessions, {d5[0]:.0f}\u2192{d5[-1]:.0f}")

    if not np.isnan(a10[-1]) and not np.isnan(a50[-1]):
        gap = a10[-1] - a50[-1]
        if gap > 15:
            obs.append(f"Short term hot: 10 DMA {gap:.0f} pts over 50 DMA")
        elif gap < -12:
            obs.append(f"Short term soft: 10 DMA {abs(gap):.0f} pts under 50 DMA")

    r5 = last.get("ratio_5d", np.nan)
    if not pd.isna(r5):
        if r5 >= 5.0:
            obs.append(f"5-day ratio {r5:.1f}: aggressive extreme")
        elif r5 <= 0.5:
            obs.append(f"5-day ratio {r5:.2f}: defensive extreme (near lows)")

    if not pd.isna(a50[-1]) and not pd.isna(a200[-1]):
        if a50[-1] > 55 and a200[-1] > 55:
            obs.append("Trends aligned: bullish")
        elif a50[-1] < 40 and a200[-1] < 45:
            obs.append("Trends aligned: bearish")
        elif a50[-1] < 45 < a200[-1]:
            obs.append("Corrective: 50 DMA soft, 200 DMA firm")

    ext = last.get("pct_extended_50dma", np.nan)
    if not pd.isna(ext) and ext > 30:
        obs.append(f"Froth: {ext:.0f}% extended above 50 DMA")

    hl = last.get("hl_ratio", np.nan)
    if not pd.isna(hl):
        if hl > 0.85 and (last.get("new_52w_high", 0) + last.get("new_52w_low", 0)) > 10:
            obs.append("52wk: highs dominate, broad strength")
        elif hl < 0.2 and (last.get("new_52w_high", 0) + last.get("new_52w_low", 0)) > 10:
            obs.append("52wk: lows dominate, broad weakness")

    u50 = last.get("up_50pct_21d", np.nan)
    if not pd.isna(u50) and u50 > 20:
        obs.append(f"Top warning: {int(u50)} up 50%/month (>20)")

    flags = []
    for _, r in g.tail(8).iterrows():
        d = r["date"].strftime("%d/%m")
        if r.get("zweig", False):
            flags.append(f"ZWEIG THRUST {d}")
        if r.get("thrust", False):
            flags.append(f"thrust {d}")
        if r.get("div_bearish", False):
            flags.append(f"bear div {d}")
        if r.get("div_bullish", False):
            flags.append(f"bull div {d}")
    if flags:
        obs.append("Signals: " + ", ".join(flags[:4]))
    return obs[:6]


def regime_runs(g):
    """Compress the regime series into runs for the timeline tab."""
    runs = []
    for _, r in g.iterrows():
        lab = regime(r)
        if runs and runs[-1]["r"] == lab:
            runs[-1]["to"] = r["date"].strftime("%d/%m/%y")
            runs[-1]["n"] += 1
        else:
            runs.append({"r": lab, "from": r["date"].strftime("%d/%m/%y"),
                         "to": r["date"].strftime("%d/%m/%y"), "n": 1})
    return runs


def load_lists(csv_dir, keep=8):
    out = {}
    for f in sorted(glob.glob(os.path.join(csv_dir, "lists", "*.json")))[-keep:]:
        try:
            out[os.path.basename(f)[:-5]] = json.load(open(f))
        except Exception:
            pass
    return out


def build_actionables(df, sizes):
    """One consolidated action read, computed from the latest session of each size universe."""
    prim = "LIQUID" if "LIQUID" in sizes else "ALL"
    g = df[df.universe == prim].sort_values("date")
    if not len(g):
        return {}
    last = g.iloc[-1]
    reg = regime(last)
    a = {"primary": ULBL.get(prim, prim), "regime": reg, "size": REGIME_SIZE.get(reg, ""),
         "checks": [], "rotation": {}, "extremes": []}

    r5 = last.get("ratio_5d", np.nan)
    a50 = last.get("pct_above_50dma", np.nan)
    a200 = last.get("pct_above_200dma", np.nan)
    t = last.get("t2108", np.nan)
    ext = last.get("pct_extended_50dma", np.nan)
    u50 = last.get("up_50pct_21d", np.nan)
    hl = last.get("hl_ratio", np.nan)
    zw = bool(g.tail(10).get("zweig", pd.Series([False])).any()) if "zweig" in g else False

    def chk(label, ok, detail):
        a["checks"].append({"k": label, "s": ok, "d": detail})

    if not pd.isna(a50):
        chk("Intermediate trend", "green" if a50 >= 58 else "amber" if a50 >= 45 else "red",
            f"{a50:.0f}% above 50 DMA")
    if not pd.isna(a200):
        chk("Long-term trend", "green" if a200 >= 55 else "amber" if a200 >= 40 else "red",
            f"{a200:.0f}% above 200 DMA")
    if not pd.isna(r5):
        chk("5-day momentum", "green" if r5 >= 3 else "amber" if r5 >= 0.9 else "red",
            f"ratio {r5:.2f}" + (" (aggressive extreme)" if r5 >= 5 else " (defensive extreme)" if r5 <= 0.5 else ""))
    if not pd.isna(hl):
        chk("New highs vs lows", "green" if hl >= 0.7 else "amber" if hl >= 0.3 else "red",
            f"H/L ratio {hl:.2f}")
    if not pd.isna(ext):
        chk("Froth (extended)", "red" if ext >= 35 else "amber" if ext >= 25 else "green",
            f"{ext:.0f}% extended >15% above 50 DMA")
    if not pd.isna(u50):
        chk("Top warning (50%/mo)", "red" if u50 > 20 else "green",
            f"{int(u50)} stocks, Bonde flags >20")
    if zw:
        a["extremes"].append("Zweig Breadth Thrust fired in the last 10 sessions, a rare and historically bullish signal")

    # rotation, if sectors present
    secs = [u for u in SECTORS if u in df["universe"].unique()]
    if secs:
        rr = []
        for u in secs:
            gg = df[df.universe == u].sort_values("date")
            if len(gg):
                rr.append((ULBL.get(u, u), gg.iloc[-1].get("pct_above_50dma", np.nan)))
        rr = [x for x in rr if not pd.isna(x[1])]
        rr.sort(key=lambda x: -x[1])
        a["rotation"] = {"lead": rr[:3], "lag": rr[-3:]}
    return a


# crossover kinds: fast=10x20 (swing/trading), mid=10x50 (rotation), slow=50x200 (position/investing)
CROSS_KINDS = [("fast", "pct_above_10dma", "pct_above_20dma"),
               ("mid", "pct_above_10dma", "pct_above_50dma"),
               ("slow", "pct_50dma_gt_200dma", None)]


def sector_crossovers(df, pools, lookback=3):
    """Recent MA-line crossovers per pool (sector or segment). Three kinds:
    fast 10x20 (swing timing), mid 10x50 (rotation), slow 50x200 (position trend)."""
    out = []
    for u in pools:
        g = df[df.universe == u].sort_values("date")
        if len(g) < 6:
            continue
        dates = g["date"].values
        for kind, fast_col, slow_col in CROSS_KINDS:
            if fast_col not in g.columns:
                continue
            if slow_col is None:
                # slow: 50>200 line crossing the 50% mark (majority stacked bullish)
                series = g[fast_col].values
                for i in range(max(1, len(g) - lookback), len(g)):
                    if np.isnan(series[i]) or np.isnan(series[i-1]):
                        continue
                    up = series[i-1] <= 50 and series[i] > 50
                    dn = series[i-1] >= 50 and series[i] < 50
                    if up or dn:
                        out.append({"u": u, "label": ULBL.get(u, u), "kind": kind,
                                    "dir": "up" if up else "dn",
                                    "when": pd.Timestamp(dates[i]).strftime("%d/%m"),
                                    "a": round(float(series[i]), 0), "b": 50,
                                    "ago": len(g) - 1 - i})
                continue
            fa, sa = g[fast_col].values, g[slow_col].values
            for i in range(max(1, len(g) - lookback), len(g)):
                if np.isnan(fa[i]) or np.isnan(sa[i]) or np.isnan(fa[i-1]) or np.isnan(sa[i-1]):
                    continue
                up = fa[i-1] <= sa[i-1] and fa[i] > sa[i]
                dn = fa[i-1] >= sa[i-1] and fa[i] < sa[i]
                if up or dn:
                    out.append({"u": u, "label": ULBL.get(u, u), "kind": kind,
                                "dir": "up" if up else "dn",
                                "when": pd.Timestamp(dates[i]).strftime("%d/%m"),
                                "a": round(float(fa[i]), 0), "b": round(float(sa[i]), 0),
                                "ago": len(g) - 1 - i})
    out.sort(key=lambda x: x["ago"])
    return out


def build(csv, out, rows, repo):
    df = pd.read_csv(csv)
    df["date"] = pd.to_datetime(df["date"])
    if "pct_20dma_gt_40dma" in df.columns and "pct_20dma_gt_50dma" not in df.columns:
        df["pct_20dma_gt_50dma"] = df["pct_20dma_gt_40dma"]
    lists = load_lists(os.path.dirname(os.path.abspath(csv)))
    stocks_path = os.path.join(os.path.dirname(os.path.abspath(csv)), "stocks.json")
    stocks = json.load(open(stocks_path)) if os.path.exists(stocks_path) else {}
    fnoset = {}
    fpath = os.path.join(os.path.dirname(os.path.abspath(csv)), "fno_universe.parquet")
    if os.path.exists(fpath):
        try:
            fdf = pd.read_parquet(fpath)
            fdf["date"] = pd.to_datetime(fdf["date"])
            latest = fdf[fdf["date"] == fdf["date"].max()]
            fnoset = {s: 1 for s in latest["symbol"].astype(str)}
        except Exception:
            pass
    fw = {}
    fwpath = os.path.join(os.path.dirname(os.path.abspath(csv)), "frameworks.json")
    if os.path.exists(fwpath):
        try:
            fw = json.load(open(fwpath))
        except Exception:
            pass
    trader = {}
    tpath = os.path.join(os.path.dirname(os.path.abspath(csv)), "trader.json")
    if os.path.exists(tpath):
        try:
            trader = json.load(open(tpath))
        except Exception:
            pass
    changes = {}
    chpath = os.path.join(os.path.dirname(os.path.abspath(csv)), "changes.json")
    if os.path.exists(chpath):
        try:
            changes = json.load(open(chpath))
        except Exception:
            pass

    present = set(df["universe"].unique())
    sizes = [u for u in SIZE_UNIVERSES if u in present]
    sects = [u for u in SECTORS if u in present]

    payload, summary, series = {}, {}, {}
    for u in sizes + sects:
        g = df[df.universe == u].sort_values("date").reset_index(drop=True)
        lim = rows if u in sizes else min(rows or 60, 60)
        gg = g.iloc[-lim:] if lim else g
        recs = []
        for _, r in gg.iterrows():
            n = int(r["universe_count"])
            vs, cs_ = [], []
            for k, _, _ in COLS:
                val = r.get(k, np.nan)
                if pd.isna(val):
                    vs.append(None); cs_.append(None); continue
                if k in PCT_FMT or k.startswith("nifty") or k in ("hl_ratio", "ratio_5d", "ratio_10d"):
                    val = round(float(val), 3 if k == "hl_ratio" else 2)
                else:
                    val = int(val)
                vs.append(val); cs_.append(shade(u, k, val, n))
            rec = [r["date"].strftime("%d/%m/%y"), r["date"].strftime("%a"), n,
                   [x for x, k in [("BEAR DIV", "div_bearish"), ("BULL DIV", "div_bullish"),
                                   ("THRUST", "thrust")] if bool(r.get(k, False))],
                   vs, cs_, r["date"].strftime("%Y-%m-%d")]
            recs.append(rec)
        recs.reverse()
        last = gg.iloc[-1]
        payload[u] = {"rows": recs, "regime": regime(last), "label": ULBL.get(u, u),
                      "asof": last["date"].strftime("%d %b %Y"), "sessions": int(len(gg)),
                      "obs": observations(gg, u)}
        # chart series: full history (capped 520 sessions ~ 2yr) so the JS can window it
        sub = g.tail(520)
        def col(c): return [None if pd.isna(v) else round(float(v), 1) for v in sub[c]] if c in sub else []
        series[u] = {"d": [x.strftime("%d/%m/%y") for x in sub["date"]],
                     "a50": col("pct_above_50dma"), "a200": col("pct_above_200dma"),
                     "t2108": col("t2108"),
                     "nifty": [None if pd.isna(v) else round(float(v), 0) for v in sub["nifty_close"]],
                     "net4": [None if pd.isna(v) else int(v) for v in sub["net_4pct"]],
                     "zweig": [i for i, (_, r) in enumerate(sub.iterrows()) if bool(r.get("zweig", False))],
                     "thr": [i for i, (_, r) in enumerate(sub.iterrows()) if bool(r.get("thrust", False))]}
        if u in sizes:
            summary[u] = {"asof": last["date"].strftime("%Y-%m-%d"), "n": int(last["universe_count"]),
                          "regime": regime(last), "a50": last.get("pct_above_50dma"),
                          "a200": last.get("pct_above_200dma"), "r5": last.get("ratio_5d"),
                          "flags": recs[0][3]}

    runs = regime_runs(df[df.universe == (sizes[0] if sizes else next(iter(present)))].sort_values("date"))
    actions = build_actionables(df, sizes)
    crossovers = sector_crossovers(df, sects)
    seg_crossovers = sector_crossovers(df, [u for u in sizes if u not in ("ALL",)])

    html = (TEMPLATE
            .replace("__DATA__", json.dumps(payload, separators=(",", ":")))
            .replace("__SERIES__", json.dumps(series, separators=(",", ":")))
            .replace("__RUNS__", json.dumps(runs, separators=(",", ":")))
            .replace("__GROUPS__", json.dumps([[gl, [[k, l, lk] for k, l, lk in cs]] for gl, cs in GROUPS]))
            .replace("__NARROW__", json.dumps(sorted(NARROW)))
            .replace("__KEYS__", json.dumps([k for k, _, _ in COLS]))
            .replace("__LISTS__", json.dumps(lists, separators=(",", ":")))
            .replace("__SIZES__", json.dumps(sizes))
            .replace("__SECTS__", json.dumps(sects))
            .replace("__ULBL__", json.dumps(ULBL))
            .replace("__ACTIONS__", json.dumps(actions, separators=(",", ":")))
            .replace("__CROSS__", json.dumps(crossovers, separators=(",", ":")))
            .replace("__SEGCROSS__", json.dumps(seg_crossovers, separators=(",", ":")))
            .replace("__STOCKS__", json.dumps(stocks, separators=(",", ":")))
            .replace("__FNOSET__", json.dumps(fnoset, separators=(",", ":")))
            .replace("__FW__", json.dumps(fw, separators=(",", ":")))
            .replace("__TRADER__", json.dumps(trader, separators=(",", ":")))
            .replace("__CHANGES__", json.dumps(changes, separators=(",", ":")))
            .replace("__REGSIZE__", json.dumps(REGIME_SIZE))
            .replace("__REPO__", repo or ""))
    with open(out, "w") as f:
        f.write(html)

    v = os.path.join(os.path.dirname(csv), "validation.txt")
    print(f"dashboard -> {out}  ({os.path.getsize(out)//1024} KB, {len(sizes)} size + {len(sects)} sector views)")
    for u, s in summary.items():
        print(f"[{u}] {s['asof']} n={s['n']} | >50DMA {s['a50']}% >200DMA {s['a200']}% "
              f"| R5 {s['r5']} | {s['regime']}" + (f" | {', '.join(s['flags'])}" if s["flags"] else ""))
    print("validation:", open(v).read().strip().replace("\n", " / ") if os.path.exists(v) else "not found")


TEMPLATE = r"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Market Breadth</title>
<style>
:root{--bg:#11161a;--pnl:#171d23;--pnl2:#1d252c;--ink:#dfe6ea;--dim:#7d8d99;--rule:#2b353e;--acc:#4fa87a}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);font:13px/1.45 ui-sans-serif,-apple-system,"Segoe UI",Roboto,sans-serif;
 -webkit-font-smoothing:antialiased}
.wrap{max-width:1780px;margin:0 auto;padding:10px 14px 44px}
.top{display:flex;flex-wrap:wrap;gap:8px 14px;align-items:baseline;justify-content:space-between;
 border-bottom:1px solid var(--rule);padding-bottom:7px}
h1{font-size:15px;margin:0;display:inline;letter-spacing:.01em;font-weight:650}
.as{color:var(--dim);font-size:10px;letter-spacing:.07em;text-transform:uppercase;margin-left:9px}
.tabs{display:flex;gap:2px;flex-wrap:wrap;margin:8px 0 9px}
.tb{padding:4px 12px;border:1px solid var(--rule);background:var(--pnl);cursor:pointer;font-size:11px;
 font-weight:600;border-radius:3px;color:var(--dim);white-space:nowrap}
.tb:hover{color:var(--ink)}
.tb[aria-selected=true]{background:var(--acc);color:#08110c;border-color:var(--acc)}
.ctl{display:flex;gap:3px;align-items:center;flex-wrap:wrap}
.us{padding:3px 9px;border:1px solid var(--rule);background:var(--pnl);cursor:pointer;font-size:10.5px;
 font-weight:600;border-radius:3px;color:var(--dim);white-space:nowrap}
.us[aria-selected=true]{background:var(--ink);color:var(--bg);border-color:var(--ink)}
select{border:1px solid var(--rule);background:var(--pnl);color:var(--ink);padding:3px 6px;border-radius:3px;font:inherit;font-size:10.5px}
.pane{display:none}.pane.on{display:block}
/* pills */
.pills{display:flex;flex-wrap:wrap;border:1px solid var(--rule);background:var(--pnl);border-radius:3px;margin-bottom:8px}
.pill{padding:5px 13px;border-right:1px solid var(--rule);min-width:86px}
.pill:last-child{border-right:0}
.pill .k{font-size:8px;letter-spacing:.11em;text-transform:uppercase;color:var(--dim)}
.pill .v{font:15px/1.25 ui-monospace,"SF Mono",Menlo,monospace;font-variant-numeric:tabular-nums;margin-top:2px}
.pill.reg{min-width:184px}.pill.reg .v{font-family:inherit;font-size:12.5px;font-weight:700}
/* bars */
.bars{display:grid;grid-template-columns:repeat(auto-fit,minmax(210px,1fr));gap:9px 18px;
 border:1px solid var(--rule);background:var(--pnl);border-radius:3px;padding:9px 13px;margin-bottom:8px}
.bg{}
.bl{font-size:8.5px;letter-spacing:.1em;text-transform:uppercase;color:var(--dim);margin-bottom:3px}
.bt{height:17px;background:#0d1216;border-radius:2px;position:relative;overflow:hidden}
.bf{height:100%;border-radius:2px}
.bv{position:absolute;right:6px;top:0;font:11px/17px ui-monospace,Menlo,monospace;color:var(--ink);text-shadow:0 0 4px #000}
/* obs */
.obs{border:1px solid var(--rule);background:var(--pnl);border-radius:3px;padding:7px 13px;margin-bottom:8px;
 font-size:11.5px;line-height:1.7;color:var(--ink)}
.obs b{color:var(--dim);font-size:8.5px;letter-spacing:.11em;text-transform:uppercase;display:block;margin-bottom:2px;font-weight:600}
.obs span{display:inline-block;margin-right:16px;color:#c3ced6}
.obs span::before{content:"";display:inline-block;width:4px;height:4px;border-radius:50%;background:var(--acc);
 margin-right:6px;vertical-align:2px}
/* table */
.tw{overflow:auto;max-height:72vh;border:1px solid var(--rule);border-radius:3px;background:var(--pnl)}
table{border-collapse:separate;border-spacing:0;width:100%;
 font:11px ui-monospace,"SF Mono",Menlo,monospace;font-variant-numeric:tabular-nums}
thead th{position:sticky;background:#0d1216;color:var(--ink);z-index:2}
tr.grp th{top:0;font-size:8px;letter-spacing:.14em;text-transform:uppercase;font-weight:600;
 padding:3px 4px;color:var(--dim);border-bottom:1px solid var(--rule)}
tr.col th{top:19px;font-size:9.5px;font-weight:600;padding:3px 4px;text-align:right;white-space:nowrap;color:#9fb0bc}
th.gs,td.gs{border-left:2px solid var(--rule)}
td{padding:2px 4px;text-align:right;white-space:nowrap;border-bottom:1px solid #202931;color:#0d1216;font-weight:600}
td.nb{color:var(--ink);font-weight:400}
td.nar,th.nar{max-width:38px}
td.d,th.d{text-align:left;position:sticky;left:0;background:var(--pnl);z-index:1;color:var(--ink);
 border-right:2px solid var(--rule);font-weight:600;padding-left:7px}
th.d{background:#0d1216;z-index:4}
td.fg{text-align:left;padding:1px 3px;background:var(--pnl)}
tr:hover td{filter:brightness(1.18)}
.fl{display:inline-block;font-size:7.5px;padding:1px 3px;border-radius:2px;margin-right:2px;
 letter-spacing:.04em;font-family:ui-sans-serif,sans-serif;font-weight:700}
.fl.BEAR{background:#c2503c;color:#fff}.fl.BULL{background:#3f9a63;color:#fff}
.fl.THRUST{background:#d8b34a;color:#241d05}
td.lk{cursor:pointer}td.lk:hover{outline:1.5px solid var(--ink);outline-offset:-1.5px}
/* charts */
.card{border:1px solid var(--rule);background:var(--pnl);border-radius:3px;padding:11px 13px;margin-bottom:9px}
.card h3{margin:0 0 8px;font-size:12px;font-weight:650;color:var(--ink);letter-spacing:.02em}
.card .cap{font-size:10.5px;color:var(--dim);margin-top:6px;line-height:1.5}
svg{display:block;width:100%}
/* compare / sector rows */
.cr{display:flex;align-items:center;gap:9px;margin-bottom:3px}
.cn{width:96px;font-size:11px;font-weight:600;text-align:right;flex-shrink:0;color:#b7c3cc}
.ct{flex:1;height:16px;background:#0d1216;border-radius:2px;position:relative;overflow:hidden;max-width:460px}
.cf{height:100%;border-radius:2px}
.cv{font:11px ui-monospace,Menlo,monospace;width:46px;text-align:right;flex-shrink:0;color:var(--ink)}
.grid2{display:grid;grid-template-columns:repeat(auto-fit,minmax(330px,1fr));gap:9px}
/* regime timeline */
.rt{display:flex;height:26px;border-radius:2px;overflow:hidden;border:1px solid var(--rule)}
.rt div{position:relative}
.rl{display:flex;gap:13px;flex-wrap:wrap;margin-top:8px;font-size:10.5px;color:var(--dim)}
.rl i{display:inline-block;width:11px;height:11px;border-radius:2px;margin-right:4px;vertical-align:-1px}
table.runs{width:100%;font:11px ui-monospace,Menlo,monospace;margin-top:11px}
table.runs td{color:var(--ink);font-weight:400;padding:3px 6px;text-align:left;border-bottom:1px solid #202931}
table.runs td.n{text-align:right;color:var(--dim)}
.tw td.n{text-align:right;color:var(--ink)}
.tw td.nb{color:var(--ink)}
/* scanner */
.sc{display:grid;grid-template-columns:repeat(auto-fill,minmax(128px,1fr));gap:5px;margin-top:9px}
.si{background:#0d1216;border:1px solid var(--rule);border-radius:2px;padding:5px 8px;font:11px ui-monospace,Menlo,monospace}
.si b{display:block;color:var(--acc);font-size:11.5px}
.si span{color:var(--dim);font-size:9.5px}
/* guide */
.gd{display:grid;grid-template-columns:repeat(auto-fill,minmax(360px,1fr));gap:9px}
.gs{border:1px solid var(--rule);background:var(--pnl);border-radius:3px;padding:11px 13px}
.gs h4{margin:0 0 7px;font-size:11px;letter-spacing:.1em;text-transform:uppercase;color:var(--acc);font-weight:650}
.gs dl{margin:0;font-size:11.5px;line-height:1.6}
.gs dt{font-weight:700;color:var(--ink);margin-top:7px}
.gs dt:first-child{margin-top:0}
.gs dd{margin:1px 0 0;color:#aab8c2}
.gs table{width:100%;font-size:11px;font-family:inherit;margin-top:4px;table-layout:fixed}
.gs table td{color:#aab8c2;font-weight:400;padding:3px 6px;border-bottom:1px solid #202931;text-align:left;
 vertical-align:top;word-break:break-word;overflow-wrap:anywhere;white-space:normal}
.gs table td:first-child{color:var(--ink);width:34%}
.gs table.two td:last-child{text-align:right;font-family:ui-monospace,Menlo,monospace;color:var(--ink);width:26%;white-space:nowrap}
.note{border-left:2px solid var(--acc);padding:7px 11px;background:#141c19;font-size:11.5px;line-height:1.6;
 color:#c3ced6;margin:9px 0}
/* overlay */
#ov{position:fixed;inset:0;background:rgba(4,8,10,.72);display:none;align-items:center;justify-content:center;z-index:9}
#ov.on{display:flex}
#bx{background:var(--pnl);border:1px solid var(--rule);border-radius:4px;max-width:760px;max-height:78vh;overflow:auto;padding:15px 18px}
#bx h3{margin:0 0 2px;font-size:13px}#bx .sub{color:var(--dim);font-size:10.5px;margin-bottom:9px}
#bx .sy{font:11px ui-monospace,Menlo,monospace;columns:4;column-gap:17px;line-height:1.75;color:#c3ced6}
#bx button{margin-top:11px;border:1px solid var(--rule);background:var(--pnl2);color:var(--ink);
 padding:4px 12px;cursor:pointer;font:inherit;font-size:10.5px;border-radius:3px}
.lg{display:flex;gap:10px;align-items:center;margin-top:8px;color:var(--dim);font-size:10px;flex-wrap:wrap}
canvas.gr{width:150px;height:10px;border-radius:2px}
.tip{position:absolute;pointer-events:none;background:#0b0f12;border:1px solid var(--rule);border-radius:3px;
 padding:5px 8px;font:10.5px ui-monospace,Menlo,monospace;color:var(--ink);opacity:0;transition:opacity .08s;
 white-space:nowrap;z-index:5;box-shadow:0 4px 12px rgba(0,0,0,.5)}
.tip .tk{color:var(--dim)}.tip .tv{color:var(--ink);font-weight:600}
.chartbox{cursor:crosshair}
.xstrip{display:flex;gap:6px;flex-wrap:wrap;padding:7px 11px;border:1px solid var(--rule);background:var(--pnl);
 border-radius:3px;margin-bottom:8px;align-items:center}
.xchip{font-size:10.5px;padding:2px 8px;border-radius:10px;font-weight:600;letter-spacing:.02em}
.xchip.up{background:#183a26;color:#6fd39a;border:1px solid #2c6b45}
.xchip.dn{background:#3a1c17;color:#e0916f;border:1px solid #6b3226}
.xmk{margin-left:5px;font-size:9px;vertical-align:1px}
.xmk.up{color:#5cc287}.xmk.dn{color:#e07a63}
.rtx{position:relative;height:14px;margin-top:2px;font:9px ui-monospace,Menlo,monospace;color:var(--dim)}
.rtx span{position:absolute;top:0;white-space:nowrap}
.jl{color:var(--acc);cursor:pointer;font-size:10px;text-decoration:underline;text-underline-offset:2px}
.jl:hover{color:#6fd39a}
.seg{display:inline-flex;align-items:center;gap:3px;border:1px solid var(--rule);border-radius:4px;padding:3px 5px;background:var(--pnl)}
.sl{font-size:8.5px;letter-spacing:.09em;text-transform:uppercase;color:var(--dim);margin-right:3px}
.sb{font-size:10.5px;padding:3px 9px;border:0;background:transparent;color:var(--dim);cursor:pointer;border-radius:3px;font-weight:600}
.sb.on{background:var(--acc);color:#08110c}.sb:hover:not(.on){color:var(--ink)}
.fno{color:#6f9fd8;font-size:10px;margin-left:4px;vertical-align:1px}
.chgstrip{display:flex;gap:6px;flex-wrap:wrap;align-items:center;padding:7px 11px;border:1px solid var(--rule);
 background:var(--pnl);border-radius:3px;margin-bottom:8px}
.chgstrip.quiet{color:var(--dim)}
.cl{font-size:8.5px;letter-spacing:.1em;text-transform:uppercase;color:var(--dim);margin-right:4px}
.chip2{font-size:10.5px;padding:2px 9px;border-radius:3px;border:1px solid var(--rule);background:#0d1216;color:#c3ced6}
.chip2 b{font-size:8.5px;letter-spacing:.05em;margin-right:4px}
.insights{border:1px solid var(--rule);background:var(--pnl);border-radius:3px;padding:8px 12px;margin-bottom:9px}
.ins{font-size:12px;line-height:1.6;color:#c3ced6}
.ins.cross{margin-top:4px;padding-top:5px;border-top:1px solid var(--rule);color:var(--ink)}
.il{font-size:8.5px;letter-spacing:.1em;font-weight:700;margin-right:8px;display:inline-block;min-width:44px}
.rmd{border-radius:3px;padding:8px 12px;margin-bottom:9px;font-size:12px;line-height:1.5}
.rmd.on{background:#3a1512;border:1.5px solid #c2503c;color:#f0b8ab}
.rmd.on b{color:#ff6b52}
.rmd.soft{background:var(--pnl);border:1px solid var(--rule);color:var(--dim);font-size:11px}
.rmd:not(.on):not(.soft){background:#3a2a12;border:1px solid #a07a30;color:#e0c68a}
.rmd code{background:#0d1216;padding:1px 5px;border-radius:2px;font-size:11px}
.rbadge{font:11px/1 ui-sans-serif;font-weight:700;padding:4px 10px;border-radius:12px;letter-spacing:.03em;white-space:nowrap}
.verdict{display:flex;justify-content:space-between;align-items:center;gap:14px;flex-wrap:wrap;
 border:2px solid var(--rule);border-radius:4px;background:var(--pnl);padding:14px 18px;margin-bottom:9px}
.vlabel{font-size:26px;font-weight:800;letter-spacing:-.01em;line-height:1}
.vsub{color:#c3ced6;font-size:12.5px;margin-top:4px}
.vright{text-align:right}
.vreg{font-size:17px;font-weight:700}
.vmeta{color:var(--dim);font-size:10.5px;margin-top:2px;letter-spacing:.03em}
.moredd{position:relative;display:inline-block}
.mm{display:none;position:absolute;right:0;top:100%;margin-top:3px;background:var(--pnl);border:1px solid var(--rule);
 border-radius:4px;z-index:8;min-width:130px;box-shadow:0 6px 16px rgba(0,0,0,.5);overflow:hidden}
.mm.on{display:block}
.mi{padding:7px 13px;font-size:11.5px;color:var(--dim);cursor:pointer;white-space:nowrap}
.mi:hover{background:var(--pnl2);color:var(--ink)}
@media(max-width:600px){.verdict{flex-direction:column;align-items:flex-start}.vright{text-align:left}}
footer{margin-top:9px;color:var(--dim);font-size:10px;line-height:1.55}
@media(max-width:760px){.pill{min-width:64px}.pill.reg{min-width:100%}.ct{max-width:180px}.cn{width:72px}}
</style></head><body><div class="wrap">
<div class="top"><div><h1>Market Breadth</h1><span class="as" id="as"></span></div>
<div style="display:flex;gap:10px;align-items:center">
<span class="rbadge" id="rbadge"></span>
<div class="ctl" id="usel"></div></div></div>
<div class="tabs" id="tabs"></div>

<div class="pane on" id="p-today"></div>
<div class="pane" id="p-trader"></div>
<div class="pane" id="p-screen"></div>
<div class="pane" id="p-table">
  <div class="pills" id="pills"></div><div class="bars" id="bars"></div><div class="obs" id="obs"></div>
  <div style="margin-bottom:7px"><select id="rows"><option value="60">60 sessions</option>
  <option value="120">120</option><option value="250" selected>250</option><option value="0">All</option></select></div>
  <div class="tw"><table id="t"><thead></thead><tbody></tbody></table></div>
  <div class="lg"><span>Fixed thresholds, India-calibrated</span><canvas class="gr" id="gc" width="150" height="10"></canvas>
  <span>bearish to bullish. Counts graded as share of universe</span><span>shaded cells with a hover outline open the stock list</span></div>
</div>

<div class="pane" id="p-charts"></div>
<div class="pane" id="p-sectors"></div>
<div class="pane" id="p-segments"></div>
<div class="pane" id="p-regime"></div>
<div class="pane" id="p-scanner"></div>

<div class="pane" id="p-guide"></div>
<div class="pane" id="p-reference"></div>

<footer id="ft"></footer></div>
<div id="ov"><div id="bx"><h3 id="bh"></h3><div class="sub" id="bs"></div><div class="sy" id="by"></div>
<button onclick="document.getElementById('ov').classList.remove('on')">Close</button></div></div>
<script>
const KEYS=__KEYS__,KI={};__KEYS__.forEach((k,i)=>KI[k]=i);
const DATA=__DATA__,SER=__SERIES__,RUNS=__RUNS__,GROUPS=__GROUPS__,NARROW=new Set(__NARROW__),
 LISTS=__LISTS__,SIZES=__SIZES__,SECTS=__SECTS__,ULBL=__ULBL__,REPO="__REPO__",ACTIONS=__ACTIONS__,REGSIZE=__REGSIZE__,CROSS=__CROSS__,SEGCROSS=__SEGCROSS__,STOCKS=__STOCKS__,FNOSET=__FNOSET__,FW=__FW__,TRADER=__TRADER__,CHANGES=__CHANGES__;
const LBL={up4:"up 4%+",dn4:"down 4%+",up10:"up 10%+",dn10:"down 10%+",hi52:"at a 52-week high",
 lo52:"at a 52-week low",up25:"up 25%+ in 21 sessions",dn25:"down 25%+ in 21 sessions",
 up25q:"up 25%+ in a quarter",dn25q:"down 25%+ in a quarter",up20_5d:"up 20%+ in 5 sessions",
 dn20_5d:"down 20%+ in 5 sessions",ext50:"more than 15% above its 50 DMA"};
let U=SIZES[0]||SECTS[0],N=250,TAB="today",CHW=520;
const RCOL={"Aggressive":"#2c8f57","Normal":"#5b8f3f","Defensive":"#9c7a30","Stand aside":"#8f3a2c","Recovery watch":"#3f7a8a","n/a":"#3a444d"};
const D_=0,WD_=1,N_=2,F_=3,V_=4,C_=5,ISO_=6;
const gv=(r,k)=>{const i=KI[k];return i==null?null:r[V_][i]};
const gc=(r,k)=>{const i=KI[k];return i==null?null:r[C_][i]};

/* ---- colour ---- */
const ST=[[0,.58,.21,.16],[.20,.72,.38,.24],[.35,.80,.61,.30],[.47,.16,.20,.24],
 [.53,.20,.30,.24],[.65,.30,.56,.33],[.80,.24,.62,.38],[1,.16,.72,.44]];
function clr(s){if(s==null)return'transparent';let a=ST[0],b=ST[ST.length-1];
 for(let i=0;i<ST.length-1;i++){if(s>=ST[i][0]&&s<=ST[i+1][0]){a=ST[i];b=ST[i+1];break}}
 const t=(s-a[0])/(b[0]-a[0]||1);
 return`rgb(${(a[1]+t*(b[1]-a[1]))*255|0},${(a[2]+t*(b[2]-a[2]))*255|0},${(a[3]+t*(b[3]-a[3]))*255|0})`}
const bc=v=>clr(Math.max(0,Math.min(1,v/100)));
const txtDark=s=>s!=null&&(s<0.38||s>0.62);
const fmt=(k,v)=>v==null?'':k==='nifty_close'?v.toLocaleString('en-IN',{maximumFractionDigits:0})
 :k==='hl_ratio'?v.toFixed(2):(k==='ratio_5d'||k==='ratio_10d')?v.toFixed(1)
 :k.startsWith('pct_')?v.toFixed(0):k==='nifty_chg_pct'?v.toFixed(2):v;

/* ---- chrome ---- */
function usel(){const e=document.getElementById('usel');
 const list=(TAB==='sectors')?SECTS:SIZES;
 e.innerHTML=list.map(u=>`<div class="us" data-u="${u}" aria-selected="${u===U}">${ULBL[u]||u}</div>`).join('');
 e.style.display=(TAB==='today'||TAB==='trader'||TAB==='screen'||TAB==='segments'||TAB==='guide'||TAB==='reference'||TAB==='regime'||TAB==='sectors')?'none':'flex';
 e.querySelectorAll('.us').forEach(t=>t.onclick=()=>{U=t.dataset.u;usel();draw()})}
const PRIMARY=[['today','Today'],['trader','Trader'],['screen','Screen'],['table','Table'],['charts','Charts']];
const MORE=[['sectors','Sectors'],['segments','Segments'],['regime','Regime'],['scanner','Scanner'],['guide','Guide'],['reference','Reference']];
function selectTab(k){TAB=k;
 document.querySelectorAll('.pane').forEach(p=>p.classList.toggle('on',p.id==='p-'+TAB));
 document.querySelectorAll('.tb').forEach(x=>x.setAttribute('aria-selected',x.dataset.t===k));
 const md=document.getElementById('moreBtn');if(md)md.setAttribute('aria-selected',MORE.some(([mk])=>mk===k));
 usel();draw();}
function tabs(){
 const prim=PRIMARY.map(([k,l])=>`<div class="tb" data-t="${k}" aria-selected="${k===TAB}">${l}</div>`).join('');
 const moreItems=MORE.map(([k,l])=>`<div class="mi" data-t="${k}">${l}</div>`).join('');
 document.getElementById('tabs').innerHTML=prim+
  `<div class="moredd"><div class="tb" id="moreBtn" aria-selected="false">More \u25be</div>
   <div class="mm" id="moreMenu">${moreItems}</div></div>`;
 document.querySelectorAll('.tb[data-t]').forEach(t=>t.onclick=()=>selectTab(t.dataset.t));
 const mb=document.getElementById('moreBtn'),mm=document.getElementById('moreMenu');
 mb.onclick=e=>{e.stopPropagation();mm.classList.toggle('on')};
 document.querySelectorAll('.mi').forEach(m=>m.onclick=()=>{mm.classList.remove('on');selectTab(m.dataset.t)});
 document.addEventListener('click',()=>mm.classList.remove('on'));}

/* ---- table pane ---- */
/* ---- screen ---- */
/* ---- trader ---- */
let SQTAB='squeeze';let XKIND='all';
function gaugeBar(pct,label,inv){if(pct==null)return `<div class="bg"><div class="bl">${label}</div><div class="bt"><div class="bv" style="right:auto;left:6px;color:var(--dim)">n/a</div></div></div>`;
 const col=inv?(pct>=80?'#c2503c':pct<=20?'#3f9a63':'#9c9a30'):(pct>=80?'#3f9a63':pct<=20?'#c2503c':'#9c9a30');
 return `<div class="bg"><div class="bl">${label}</div><div class="bt"><div class="bf" style="width:${pct}%;background:${col}"></div><div class="bv">${pct}%</div></div></div>`;}
function traderPane(){const T=TRADER,el=document.getElementById('p-trader');
 if(!T||!T.index){el.innerHTML='<div class="card"><h3>Trader</h3><div class="cap">trader.json not found. Run trader.py in the pipeline after ingest. VIX and index-squeeze signals need index_ohlc.parquet; stock squeeze needs the OHLC-widened price store (one backfill re-run).</div></div>';return}
 const v=T.index.vix||{},ix=T.index.indices||[],fb=T.fno||{};
 // VIX card
 let ivState=v.ivrank==null?'':v.ivrank>=70?'RICH — sellers favoured':v.ivrank<=30?'CHEAP — buyers favoured':'MID';
 let ivCol=v.ivrank==null?'var(--dim)':v.ivrank>=70?'#e07a63':v.ivrank<=30?'#6fd39a':'#c9a04a';
 const hv=v.hv||{};
 const vixCard=`<div class="card"><h3>India VIX &mdash; index options vol</h3>
  <div style="display:flex;gap:16px;flex-wrap:wrap;align-items:baseline;margin-bottom:9px">
   <div><span style="font-size:24px;font-weight:800">${v.level??'--'}</span><span class="cap"> VIX level</span></div>
   <div style="color:${ivCol};font-weight:700">${ivState}</div></div>
  <div class="bars" style="border:0;padding:0;background:none;margin-bottom:8px">
   ${gaugeBar(v.ivrank,'IV Rank (52wk)')}${gaugeBar(v.ivpctile,'IV Percentile')}</div>
  <table class="runs"><tbody>
   <tr><td>Realised vol 10d / 20d / 30d</td><td class="n">${hv['10']??'--'} / ${hv['20']??'--'} / ${hv['30']??'--'}%</td></tr>
   <tr><td>Variance risk premium (VIX &minus; 30d HV)</td><td class="n" style="color:${v.vrp_30d>0?'#6fd39a':'#e07a63'}">${v.vrp_30d==null?'--':(v.vrp_30d>0?'+':'')+v.vrp_30d} ${v.vrp_pctile!=null?'('+v.vrp_pctile+' pctile)':''}</td></tr>
   <tr><td>Expected move, 1 week (1&sigma;)</td><td class="n">${v.exp_move_1w_pct??'--'}%${v.exp_move_1w_pts?' &middot; '+v.exp_move_1w_pts+' pts':''}</td></tr>
   ${v.divergence?'<tr><td style="color:#e07a63">VIX-Nifty divergence</td><td class="n" style="color:#e07a63">both rising &mdash; rally options distrust</td></tr>':''}
  </tbody></table>
  <div class="cap" style="margin-top:6px">IV Rank high = premium rich (favours selling); low = cheap (favours buying). VRP is context, not a standalone trigger. 30d HV is the VIX-matched window. <a class="jl" onclick="jump('reference')">method &rarr;</a></div></div>`;
 // index squeeze cards
 const idxCards=ix.map(i=>{const st=i.state,col=st==='squeeze'?'#c9a04a':st==='expansion'?'#3f9a63':'var(--dim)';
  return `<div class="card" style="flex:1;min-width:200px"><h3>${i.label}</h3>
   <div style="font-size:18px;font-weight:700;color:${col};text-transform:uppercase">${st}</div>
   ${i.atr_pctile!=null?`<div class="cap">ATR percentile ${i.atr_pctile}% &middot; ${i.atr_pct}% of price</div>`:'<div class="cap">needs index OHLC (arrives after next run)</div>'}</div>`;}).join('');
 // fno squeeze / events
 const evRows=(fb.events||[]).map(e=>`<tr><td class="d">${e.s}${FNOSET&&FNOSET[e.s]?'':''}</td><td class="n" style="color:${e.chg>0?'#6fd39a':'#e07a63'}">${e.chg>0?'+':''}${e.chg}%</td><td class="n">${e.px}</td></tr>`).join('');
 const sqRows=(fb.squeeze||[]).slice(0,30).map(x=>`<tr><td class="d">${x.s}${FNOSET&&FNOSET[x.s]?'':''}</td><td class="n">${x.atr_pctile}%</td><td class="n">${x.atr_pct}%</td><td class="n" style="color:${x.lean==='up'?'#6fd39a':'#e07a63'}">${x.lean==='up'?'\u2191':'\u2193'}</td><td class="n">${x.px}</td><td class="n" style="color:${x.chg>0?'#6fd39a':'#e07a63'}">${x.chg>0?'+':''}${x.chg}</td></tr>`).join('');
 const noOhlc=!fb.has_ohlc;
 el.innerHTML=`
 <div class="note">Volatility context for F&O. Every gauge here is a regime read, not a signal to enter. Confirm with your own chart, option chain and event calendar. Data ${T.asof||''}.</div>
 ${vixCard}
 <div style="display:flex;gap:9px;flex-wrap:wrap;margin-bottom:9px">${idxCards}</div>
 <div class="card">
  <div style="display:flex;gap:4px;margin-bottom:8px">
   <button class="sb ${SQTAB==='squeeze'?'on':''}" onclick="SQTAB='squeeze';traderPane()">Squeeze scan</button>
   <button class="sb ${SQTAB==='events'?'on':''}" onclick="SQTAB='events';traderPane()">&plusmn;8% events</button></div>
  ${(fb.fired&&fb.fired.length)?`<div class="obs" style="margin-bottom:8px"><b style="color:#d8b34a">Squeeze FIRES today \u2014 coil just broke</b>${fb.fired.slice(0,10).map(f=>`<span>${f.s} <span style="color:${f.lean==='up'?'#6fd39a':'#e07a63'}">${f.lean==='up'?'\u2191':'\u2193'}</span> ${f.chg>0?'+':''}${f.chg}%</span>`).join('')}</div>`:''}
  ${SQTAB==='squeeze'?(noOhlc?`<div class="cap">Stock squeeze scan needs the OHLC-widened price store. It activates one backfill re-run after you deploy the new ingest. Until then, index squeeze (above) and event movers work.</div>`
    :`<div class="obs"><b>Most coiled F&O stocks</b><span>Lowest ATR percentile = tightest range = expansion setup loading</span></div>
    <div class="tw" style="max-height:52vh"><table><thead><tr><th class="d">Symbol</th><th>ATR %ile</th><th>ATR%</th><th>Lean</th><th>Price</th><th>Day%</th></tr></thead><tbody>${sqRows}</tbody></table></div>`)
   :(evRows?`<div class="obs"><b>Today&rsquo;s &plusmn;8% movers in F&O</b><span>Event-driven. Check the news before fading or chasing.</span></div>
    <div class="tw"><table><thead><tr><th class="d">Symbol</th><th>Change</th><th>Price</th></tr></thead><tbody>${evRows}</tbody></table></div>`
    :`<div class="cap">No F&O stock moved more than 8% today. On event-heavy days (results, news) this list populates.</div>`)}
 </div>`;}

let RSMODE='rsu',SCRSTRICT=true,SCRSEC='ALL',SCRFW=false,RSTYPE='rank';
function fwList(sym){return (FW&&FW.map&&FW.map[sym])||[];}
function fwCell(sym){const l=fwList(sym);if(!l.length)return '<span style="color:#4a5560">&mdash;</span>';
 const col=l.length>=3?'#6fd39a':l.length>=2?'#c3d68a':'#9fb0bc';
 return `<span style="color:${col}" title="${l.join(', ')}">${l.length>=2?'\u2605 ':''}${l.length} fw</span>`;}
function screenPane(){const el=document.getElementById('p-screen');
 if(!STOCKS||!STOCKS.stocks){el.innerHTML='<div class="card"><h3>Stock screen</h3><div class="cap">stocks.json not found. Run screen.py in the pipeline after ingest to generate the Stage-2 / RS screen.</div></div>';return}
 const A=ACTIONS;
 const rsLabel={rsu:'vs Universe',rss:'vs Sector',rsn:'vs Nifty 50'};
 let rows=STOCKS.stocks.filter(x=>SCRSTRICT?x.strict:true);
 if(SCRFW)rows=rows.filter(x=>fwList(x.s).length>0);
 // sector filter
 const secs=[...new Set(STOCKS.stocks.map(x=>x.sec).filter(Boolean))].sort();
 if(SCRSEC!=='ALL')rows=rows.filter(x=>x.sec===SCRSEC);
 // sort by chosen RS desc, nulls last
 const mkey={rsu:'mru',rss:'mrs',rsn:'mrn'}[RSMODE];
 const getv=x=>RSTYPE==='mans'?(x[mkey]?x[mkey][0]:null):x[RSMODE];
 rows=rows.slice().sort((a,b)=>{const av=getv(a),bv=getv(b);
  if(av==null&&bv==null)return 0;if(av==null)return 1;if(bv==null)return -1;return bv-av;});
 const cap=rows.length;rows=rows.slice(0,120);
 const stg={'2':'#3f9a63','1':'#9c9a30','3':'#d8875a','4':'#c2503c','?':'#5d6b63'};
 const rcell=v=>v==null?'<td class="nb">&mdash;</td>':`<td style="background:${clr(Math.max(0,Math.min(1,(v-1)/98)))};${(v<38||v>62)?'color:#e8eef2':''}">${v}</td>`;
 const mcell=m=>{if(!m)return '<td class="nb">&mdash;</td>';const v=m[0],up=m[1]>0;
  const col=v>0?(up?'#6fd39a':'#c3d68a'):(up?'#e8b979':'#e07a63');
  return `<td class="nb" style="color:${col}" title="${up?'rising':'falling'}">${v>0?'+':''}${v.toFixed(0)}${up?'\u2191':'\u2193'}</td>`;};
 const body=rows.map(x=>`<tr>
  <td class="d">${x.s}${(typeof FNOSET!=='undefined'&&FNOSET[x.s])?'<span class="fno" title="F&O stock">&#8857;</span>':''}</td>
  <td class="fg">${x.sec||'&mdash;'}</td>
  <td style="color:${stg[x.stg]};font-weight:700">${x.stg}</td>
${RSTYPE==='mans'?(mcell(x.mru)+mcell(x.mrs)+mcell(x.mrn)):(rcell(x.rsu)+rcell(x.rss)+rcell(x.rsn))}
  <td class="nb" style="color:${x.fh!=null&&x.fh>-8?'#7fd6a0':'#c3ced6'}">${x.fh==null?'':x.fh.toFixed(1)}</td>
  <td class="nb">${x.fl==null?'':'+'+x.fl}</td>
  <td class="nb">${x.px==null?'':x.px.toLocaleString('en-IN')}</td>
  <td class="nb">${x.strict?'<span class="fl BULL">8/8</span>':'<span style="color:#9fb0bc">'+x.pc+'/8</span>'}</td>
  <td class="nb" style="text-align:left">${fwCell(x.s)}</td></tr>`).join('');
 const marketNote=A&&(A.regime==='Stand aside'||A.regime==='Defensive')
  ?`<div class="note" style="border-color:#c2503c">Market regime is <b>${A.regime}</b>. O&rsquo;Neil, Minervini and Bonde all say the same thing: the best stock in a weak tape still fails. Treat this list as a watchlist, not a buy list, until the regime turns.</div>`
  :`<div class="note">Market regime is <b>${A?A.regime:'?'}</b>. Names below pass a Stage-2 trend template. Cross-check each against liquidity and your own fundamentals before acting.</div>`;
 el.innerHTML=`
 <div style="display:flex;gap:6px;flex-wrap:wrap;margin-bottom:8px;align-items:center">
  <div class="seg"><span class="sl">RS type</span>
   <button class="sb ${RSTYPE==='rank'?'on':''}" onclick="RSTYPE='rank';screenPane()">Rank (IBD)</button>
   <button class="sb ${RSTYPE==='mans'?'on':''}" onclick="RSTYPE='mans';screenPane()">Mansfield</button></div>
  <div class="seg"><span class="sl">Benchmark</span>
   ${['rsu','rss','rsn'].map(m=>`<button class="sb ${RSMODE===m?'on':''}" onclick="RSMODE='${m}';screenPane()">${rsLabel[m]}</button>`).join('')}</div>
  <div class="seg"><span class="sl">Filter</span>
   <button class="sb ${SCRSTRICT?'on':''}" onclick="SCRSTRICT=true;screenPane()">Strict 8/8</button>
   <button class="sb ${!SCRSTRICT?'on':''}" onclick="SCRSTRICT=false;screenPane()">Relaxed</button></div>
  <div class="seg"><span class="sl">Playbook</span>
   <button class="sb ${!SCRFW?'on':''}" onclick="SCRFW=false;screenPane()">All</button>
   <button class="sb ${SCRFW?'on':''}" onclick="SCRFW=true;screenPane()">In a framework</button></div>
  <select onchange="SCRSEC=this.value;screenPane()" style="margin-left:auto">
   <option value="ALL"${SCRSEC==='ALL'?' selected':''}>All sectors</option>
   ${secs.map(sc=>`<option value="${sc}"${SCRSEC===sc?' selected':''}>${sc}</option>`).join('')}</select>
 </div>
 ${marketNote}
 <div class="obs"><b>Key observations</b>
  <span>${STOCKS.n_strict} names pass strict Stage-2</span>
  <span>Universe with RS: ${STOCKS.universe}</span>
  <span>Showing ${rows.length} of ${cap} ${SCRSTRICT?'strict':'passing'}${SCRSEC!=='ALL'?' in '+SCRSEC:''}, ranked ${rsLabel[RSMODE]}</span>
  <span>as of ${STOCKS.asof}</span>
  ${(()=>{const c=STOCKS.stocks.filter(x=>x.strict&&fwList(x.s).length>0);return c.length?`<span style="color:#6fd39a">${c.length} strict names also in your Playbook</span>`:'';})()}</div>
 <div class="tw"><table><thead><tr>
  <th class="d">Symbol</th><th>Sector</th><th>Stg</th>
<th>${RSTYPE==='mans'?'MR-U':'RS-U'}</th><th>${RSTYPE==='mans'?'MR-Sec':'RS-Sec'}</th><th>${RSTYPE==='mans'?'MR-N50':'RS-N50'}</th><th>% frm hi</th><th>% frm lo</th><th>Price</th><th>Template</th><th>Playbook</th></tr></thead>
  <tbody>${body}</tbody></table></div>
 <div class="cap" style="margin-top:6px">Stage: <span style="color:#3f9a63">2 advancing</span> &middot; <span style="color:#9c9a30">1 basing</span> &middot; <span style="color:#d8875a">3 topping</span> &middot; <span style="color:#c2503c">4 declining</span>.
  RS is a 1-99 percentile of weighted 3/6/12-month return. RS-Sec ranks within the stock&rsquo;s Nifty sector; blank means the stock is not in a tracked sector index.
  Playbook column: how many of your framework screens the name appears in. &#9733; marks 2+, the multi-screen consensus. &#8857; on a symbol marks an F&O stock.
  RS type: <b>Rank</b> is the IBD 1-99 percentile (O&rsquo;Neil/Minervini). <b>Mansfield</b> is Weinstein&rsquo;s benchmark-relative line: % above the stock&rsquo;s own 52-week ratio to the benchmark, with &#8593; rising / &#8595; falling. Positive and rising is the leadership zone; positive but falling is decelerating.
  <a class="jl" onclick="jump('reference')">method &amp; sources &rarr;</a></div>`;}

function pills(d){const r=d.rows[0],p=[];
 p.push(`<div class="pill reg"><div class="k">Regime &middot; ${d.label}</div><div class="v" style="color:${RCOL[d.regime]||'inherit'}">${d.regime}</div></div>`);
 [['pct_above_10dma','&gt;10 DMA','%'],['pct_above_50dma','&gt;50 DMA','%'],['pct_above_200dma','&gt;200 DMA','%'],
  ['ratio_5d','5-day ratio',''],['hl_ratio','H/L ratio','']].forEach(([k,l])=>{
  const v=gv(r,k);p.push(`<div class="pill"><div class="k">${l}</div><div class="v">${
   v==null?'--':(k.startsWith('pct_')?v.toFixed(1)+'%':v.toFixed(2))}</div></div>`)});
 const n4=gv(r,'net_4pct');
 p.push(`<div class="pill"><div class="k">Net 4%</div><div class="v" style="color:${n4>0?'#5cc287':'#e07a63'}">${n4>0?'+':''}${n4}</div></div>`);
 p.push(`<div class="pill"><div class="k">Universe</div><div class="v">${r[N_]}</div></div>`);
 if(r[F_].length)p.push(`<div class="pill"><div class="k">Flags</div><div class="v">${
  r[F_].map(f=>`<span class="fl ${f.split(' ')[0]}">${f}</span>`).join(' ')}</div></div>`);
 document.getElementById('pills').innerHTML=p.join('');
 document.getElementById('as').textContent=`as of ${d.asof} · ${r[N_]} stocks · ${d.sessions} sessions`}
function bars(d){const r=d.rows[0];
 document.getElementById('bars').innerHTML=[['pct_above_10dma','% above 10 DMA'],
  ['pct_above_50dma','% above 50 DMA'],['pct_above_200dma','% above 200 DMA'],
  ['pct_extended_50dma','% extended, >15% above 50 DMA']].map(([k,l])=>{const v=gv(r,k);
  if(v==null)return'';return`<div class="bg"><div class="bl">${l}</div><div class="bt">
  <div class="bf" style="width:${v}%;background:${k==='pct_extended_50dma'?'#c9a04a':bc(v)}"></div>
  <div class="bv">${v.toFixed(1)}%</div></div></div>`}).join('')}
function obsP(d,el){const e=document.getElementById(el||'obs');
 if(!d.obs||!d.obs.length){e.style.display='none';return}
 e.style.display='';e.innerHTML='<b>Key observations</b>'+d.obs.map(o=>`<span>${o}</span>`).join('')}
function head(){const g=['<th class="d" rowspan="2">Date</th><th rowspan="2">Flags</th>'],c=[];
 GROUPS.forEach(([gl,cs],i)=>{g.push(`<th colspan="${cs.length}" class="${i?'gs':''}">${gl}</th>`);
  cs.forEach(([k,l],j)=>c.push(`<th class="${!j&&i?'gs ':''}${NARROW.has(k)?'nar':''}">${l}</th>`))});
 document.querySelector('#t thead').innerHTML=`<tr class="grp">${g.join('')}</tr><tr class="col">${c.join('')}</tr>`}
function body(d){document.querySelector('#t tbody').innerHTML=(N?d.rows.slice(0,N):d.rows).map(r=>{
 const td=[];GROUPS.forEach(([gl,cs],i)=>cs.forEach(([k,l,lk],j)=>{const s=gc(r,k),vv=gv(r,k);
  const cl=[!j&&i?'gs':'',NARROW.has(k)?'nar':'',s==null?'nb':'',lk&&vv>0?'lk':''].filter(Boolean).join(' ');
  td.push(`<td class="${cl}" style="${s==null?'':'background:'+clr(s)+(txtDark(s)?';color:#e8eef2':'')}"${
   lk&&vv>0?` data-i="${r[ISO_]}" data-k="${lk}"`:''}>${fmt(k,vv)}</td>`)}));
 return`<tr><td class="d">${r[D_]} ${r[WD_]}</td><td class="fg">${
  r[F_].map(f=>`<span class="fl ${f.split(' ')[0]}">${f}</span>`).join('')}</td>${td.join('')}</tr>`}).join('');
 document.querySelectorAll('td.lk').forEach(t=>t.onclick=()=>show(t.dataset.i,t.dataset.k))}

/* ---- charts ---- */
function line(vals,w,h,lo,hi,col,fill){const n=vals.length;if(!n)return'';
 const pts=vals.map((v,i)=>v==null?null:[i/(n-1)*w,h-(v-lo)/(hi-lo)*h]).filter(Boolean);
 const d=pts.map((p,i)=>(i?'L':'M')+p[0].toFixed(1)+' '+p[1].toFixed(1)).join(' ');
 return(fill?`<path d="${d} L ${w} ${h} L 0 ${h} Z" fill="${fill}"/>`:'')+
  `<path d="${d}" fill="none" stroke="${col}" stroke-width="1.4"/>`}
function chartPane(){const s0=SER[U],d=DATA[U];if(!s0)return;
 const W=1000,H=170;
 const tot=s0.d.length,st=CHW?Math.max(0,tot-CHW):0;
 const s={d:s0.d.slice(st),a50:s0.a50.slice(st),a200:s0.a200.slice(st),t2108:(s0.t2108||[]).slice(st),
   nifty:s0.nifty.slice(st),net4:s0.net4.slice(st),
   zweig:(s0.zweig||[]).filter(i=>i>=st).map(i=>i-st),thr:(s0.thr||[]).filter(i=>i>=st).map(i=>i-st)};
 const n=s.d.length;
 const nz=s.nifty.filter(v=>v!=null),nlo=Math.min(...nz)*0.99,nhi=Math.max(...nz)*1.01;
 const bands=[[0,15,'#3a1c17'],[15,30,'#41291c'],[30,45,'#3d3520'],[45,60,'#25341f'],[60,100,'#1b3a26']];
 const bandsSvg=bands.map(([a,b,c])=>`<rect x="0" y="${H-b/100*H}" width="${W}" height="${(b-a)/100*H}" fill="${c}" opacity=".55"/>`).join('');
 const gridx=[0,.25,.5,.75,1].map(f=>`<line x1="${f*W}" y1="0" x2="${f*W}" y2="${H}" stroke="#2b353e" stroke-width=".6"/>`).join('');
 const lbl=[0,.25,.5,.75,1].map(f=>`<text x="${f*W}" y="${H+13}" fill="#7d8d99" font-size="10" text-anchor="${f?f===1?'end':'middle':'start'}">${s.d[Math.round(f*(n-1))]}</text>`).join('');
 const n4max=Math.max(1,...s.net4.map(v=>Math.abs(v||0)));
 const n4bars=s.net4.map((v,i)=>{const x=i/n*W,bw=Math.max(1.2,W/n-0.6),hh=Math.abs(v||0)/n4max*46;
  return`<rect x="${x.toFixed(1)}" y="${(v>=0?50-hh:50).toFixed(1)}" width="${bw.toFixed(1)}" height="${hh.toFixed(1)}" fill="${v>=0?'#3f9a63':'#c2503c'}"/>`}).join('');
 document.getElementById('p-charts').innerHTML=`
 <div style="margin-bottom:7px"><select id="chw">
  <option value="120">6 months</option><option value="250">1 year</option>
  <option value="520" selected>2 years</option><option value="0">All history</option></select></div>
 <div class="obs" id="obsC"></div>
 <div class="card"><h3>Participation vs price &mdash; ${d.label}</h3>
  <div class="chartbox" id="cb1" style="position:relative">
  <svg id="svg1" viewBox="0 0 ${W} ${H+20}" preserveAspectRatio="none" style="height:200px">${bandsSvg}${gridx}
   ${line(s.a50,W,H,0,100,'#5cc287')}${line(s.a200,W,H,0,100,'#6f9fd8')}
   ${(s.t2108&&s.t2108.length?line(s.t2108,W,H,0,100,'#b98bd8'):'')}
   ${line(s.nifty,W,H,nlo,nhi,'#d8b34a')}
   ${s.zweig.map(i=>`<circle cx="${(i/(n-1)*W).toFixed(1)}" cy="6" r="4" fill="#e8d24a" stroke="#111"/>`).join('')}
   ${s.thr.map(i=>`<circle cx="${(i/(n-1)*W).toFixed(1)}" cy="6" r="2.4" fill="#5cc287"/>`).join('')}${lbl}
   <line id="ch1" x1="0" y1="0" x2="0" y2="${H}" stroke="#e8eef2" stroke-width="1" opacity="0"/></svg>
  <div class="tip" id="tip1"></div></div>
  <div class="cap"><span style="color:#5cc287">&#9644;</span> % above 50 DMA &nbsp;
   <span style="color:#b98bd8">&#9644;</span> T2108 (% above 40 DMA) &nbsp;
   <span style="color:#6f9fd8">&#9644;</span> % above 200 DMA &nbsp;
   <span style="color:#d8b34a">&#9644;</span> Nifty 50, rescaled &nbsp;
   <span style="color:#e8d24a">&#9679;</span> Zweig thrust &nbsp;<span style="color:#5cc287">&#9679;</span> 4% thrust.
   Background bands mark regime zones: red below 30, amber 30 to 45, green above 60.
   Where the gold line rises while the green line falls, the index is being carried by fewer stocks.</div></div>
 <div class="card"><h3>Net 4% movers</h3>
  <div class="chartbox" id="cb2" style="position:relative">
  <svg id="svg2" viewBox="0 0 ${W} 100" preserveAspectRatio="none" style="height:110px">
   <line x1="0" y1="50" x2="${W}" y2="50" stroke="#2b353e"/>${n4bars}
   <line id="ch2" x1="0" y1="0" x2="0" y2="100" stroke="#e8eef2" stroke-width="1" opacity="0"/></svg>
  <div class="tip" id="tip2"></div></div>
  <div class="cap">Stocks up 4% minus stocks down 4%, each session. Clusters of tall green bars after a decline
   are the thrust signature; sustained red under a flat index is distribution.</div></div>`;
 const sel=document.getElementById('chw');if(sel){sel.value=String(CHW);sel.onchange=e=>{CHW=+e.target.value;chartPane()}}
 wireTip('cb1','svg1','ch1','tip1',n,i=>[
   ['Date',s.d[i]],['% >50 DMA',fmtp(s.a50[i])],['T2108',fmtp(s.t2108[i])],
   ['% >200 DMA',fmtp(s.a200[i])],['Nifty',s.nifty[i]!=null?Math.round(s.nifty[i]).toLocaleString('en-IN'):'']]);
 wireTip('cb2','svg2','ch2','tip2',n,i=>[['Date',s.d[i]],['Net 4%',(s.net4[i]>0?'+':'')+(s.net4[i]??'')]]);
 obsP(d,'obsC')}
function fmtp(v){return v==null?'':v.toFixed(1)+'%'}
function wireTip(box,svg,ch,tip,n,rowFn){
 const b=document.getElementById(box),sv=document.getElementById(svg),
  cl=document.getElementById(ch),tp=document.getElementById(tip);
 if(!b||!sv)return;
 b.onmousemove=e=>{const r=b.getBoundingClientRect();const fx=(e.clientX-r.left)/r.width;
  let i=Math.round(fx*(n-1));i=Math.max(0,Math.min(n-1,i));
  const vb=sv.viewBox.baseVal.width,x=i/(n-1)*vb;
  cl.setAttribute('x1',x);cl.setAttribute('x2',x);cl.setAttribute('opacity','.5');
  const rows=rowFn(i).filter(([k,v])=>v!=='');
  tp.innerHTML=rows.map(([k,v])=>`<span class="tk">${k}</span> <span class="tv">${v}</span>`).join('<br>');
  tp.style.opacity='1';const left=fx>0.6?fx*r.width-tp.offsetWidth-12:fx*r.width+12;
  tp.style.left=Math.max(2,left)+'px';tp.style.top='6px';};
 b.onmouseleave=()=>{cl.setAttribute('opacity','0');tp.style.opacity='0';};}

/* ---- sectors ---- */
function sectorPane(){if(!SECTS.length){document.getElementById('p-sectors').innerHTML=
  '<div class="card"><h3>Sector breadth</h3><div class="cap">Sector data has not been ingested yet. Run the pipeline with the updated ingest script to populate the twelve NSE sectoral indices.</div></div>';return}
 const rank=SECTS.map(u=>({u,r:DATA[u]?DATA[u].rows[0]:null})).filter(x=>x.r)
  .sort((a,b)=>(gv(b.r,'pct_above_50dma')||0)-(gv(a.r,'pct_above_50dma')||0));
 const top=rank.slice(0,3).map(x=>ULBL[x.u]).join(', '),bot=rank.slice(-3).map(x=>ULBL[x.u]).join(', ');
 const XMAP={};CROSS.forEach(c=>{XMAP[c.u]=c.dir});
 const rows=(m,sortSelf)=>{let R=rank;
  if(sortSelf){R=rank.slice().sort((a,b)=>(gv(b.r,m)||0)-(gv(a.r,m)||0));}
  return R.map(x=>{const v=gv(x.r,m);if(v==null)return'';
  const mk=XMAP[x.u]?`<span class="xmk ${XMAP[x.u]}">${XMAP[x.u]==='up'?'\u25b2':'\u25bc'}</span>`:'';
  return`<div class="cr"><span class="cn">${ULBL[x.u]}${mk}</span><div class="ct">
  <div class="cf" style="width:${v}%;background:${bc(v)}"></div></div><span class="cv">${v.toFixed(0)}%</span></div>`}).join('');};
 const kindLbl={fast:'10\u00d720 fast',mid:'10\u00d750',slow:'50\u00d7200 slow'};
 const cf=(typeof XKIND==='undefined')?'all':XKIND;
 const cross=CROSS.filter(c=>cf==='all'||c.kind===cf);
 const ups=cross.filter(c=>c.dir==='up'),dns=cross.filter(c=>c.dir==='dn');
 const strip=`<div class="xstrip">
   <span class="sl">Crossovers</span>
   ${['all','fast','mid','slow'].map(k=>`<button class="sb ${cf===k?'on':''}" onclick="XKIND='${k}';sectorPane()">${k==='all'?'All':kindLbl[k]||k}</button>`).join('')}
   ${cross.length?cross.slice(0,8).map(c=>`<span class="xchip ${c.dir}" title="${kindLbl[c.kind]||c.kind}">${c.dir==='up'?'\u25b2':'\u25bc'} ${c.label} <span style="opacity:.6">${kindLbl[c.kind]||''}</span> ${c.when}</span>`).join('')
     :'<span style="color:var(--dim)">none in last 3 sessions</span>'}</div>`;
 const cbnobs=[];
 cbnobs.push(`<span>Lead 50 DMA: ${top}</span>`);
 cbnobs.push(`<span>Lag: ${bot}</span>`);
 cbnobs.push(`<span>Spread: ${(gv(rank[0].r,'pct_above_50dma')-gv(rank[rank.length-1].r,'pct_above_50dma')).toFixed(0)} pts</span>`);
 if(ups.length)cbnobs.push(`<span style="color:#5cc287">Turning up: ${ups.map(c=>c.label).join(', ')}</span>`);
 if(dns.length)cbnobs.push(`<span style="color:#e07a63">Rolling over: ${dns.map(c=>c.label).join(', ')}</span>`);
 document.getElementById('p-sectors').innerHTML=`
 <div class="obs"><b>Key observations</b>${cbnobs.join('')}</div>
 ${strip}
 <div class="grid2">
  <div class="card"><h3>% above 50 DMA, by sector</h3>${rows('pct_above_50dma')}
   <div class="cap">Intermediate trend health. Sectors above 60 are participating; below 30 are being sold.</div></div>
  <div class="card"><h3>% above 200 DMA, by sector</h3>${rows('pct_above_200dma')}
   <div class="cap">Long-term structure. A sector strong here but weak on the 50 DMA is correcting inside an uptrend, which is where pullback entries live.</div></div></div>
 <div class="grid2" style="margin-top:9px">
  <div class="card"><h3>% above 10 DMA, by sector &mdash; who is turning now</h3>${rows('pct_above_10dma','st')}
   <div class="cap">Short-term momentum. Ranked the same way as the 50 DMA panel above, so a sector jumping up this list versus its 50 DMA rank is accelerating; slipping down is stalling. This is the earliest rotation tell.</div></div>
  <div class="card"><h3>% above 20 DMA, by sector</h3>${rows('pct_above_20dma','st')}
   <div class="cap">The bridge between the 10 and 50 DMA reads. A sector green on 10 and 20 but amber on 50 is in the first leg of a turn.</div></div></div>
 <div class="note">Rotation read: work down the four panels from short to long. A sector high on 10/20 DMA but low on 50/200 is turning up and worth a watch. A sector still high on 50/200 but fading on 10/20 is where distribution starts. The cleanest longs sit where all four align.</div>`}

/* ---- compare ---- */
function comparePane(){/* segments */
 const segStrip=(typeof SEGCROSS!=='undefined'&&SEGCROSS.length)?`<div class="xstrip"><span class="sl">Segment crossovers</span>${SEGCROSS.slice(0,8).map(c=>{const kl={fast:'10\u00d720',mid:'10\u00d750',slow:'50\u00d7200'};return `<span class="xchip ${c.dir}">${c.dir==='up'?'\u25b2':'\u25bc'} ${c.label} <span style="opacity:.6">${kl[c.kind]||''}</span> ${c.when}</span>`;}).join('')}</div>`:'';
 const M=[['pct_above_50dma','% above 50 DMA'],['pct_above_200dma','% above 200 DMA'],
  ['pct_above_10dma','% above 10 DMA'],['pct_50dma_gt_200dma','50 DMA above 200 DMA']];
 const get=(u,k)=>DATA[u]?gv(DATA[u].rows[0],k):null;
 const a=get('ALL','pct_above_50dma'),f=get('FNO','pct_above_50dma');
 const sm=get('SMALLCAP250','pct_above_50dma'),n5=get('NIFTY50','pct_above_50dma');
 const o=[];
 if(a!=null&&f!=null)o.push(`<span>Liquid F&amp;O basket vs full market on 50 DMA: ${f.toFixed(0)}% vs ${a.toFixed(0)}%, gap ${(f-a).toFixed(0)} pts</span>`);
 if(sm!=null&&n5!=null)o.push(`<span>Smallcap 250 vs Nifty 50: ${sm.toFixed(0)}% vs ${n5.toFixed(0)}%, ${sm>n5?'risk appetite broadening down the cap curve':'large caps holding better, defensive tilt'}</span>`);
 document.getElementById('p-segments').innerHTML=`<div class="obs"><b>Key observations</b>${o.join('')}</div>`+segStrip+
 `<div class="grid2">`+M.map(([k,l])=>`<div class="card"><h3>${l}</h3>`+
  SIZES.map(u=>{const v=get(u,k);if(v==null)return'';
   return`<div class="cr"><span class="cn">${ULBL[u]}</span><div class="ct">
   <div class="cf" style="width:${v}%;background:${bc(v)}"></div></div><span class="cv">${v.toFixed(0)}%</span></div>`}).join('')
  +`</div>`).join('')+`</div>`}

/* ---- regime timeline ---- */
let RTW=520;  // regime timeline window; default 2 years
function parseDMY(x){const p=x.split('/');return new Date(2000+ +p[2], +p[1]-1, +p[0]);}
function regimePane(){const RC=RCOL;
 let R=RUNS;
 if(RTW){let acc=0;R=[];for(let i=RUNS.length-1;i>=0;i--){R.unshift(RUNS[i]);acc+=RUNS[i].n;if(acc>=RTW)break;}}
 const tot=R.reduce((s,r)=>s+r.n,0);
 const bar=R.map(r=>`<div style="flex:${r.n};background:${RC[r.r]}" title="${r.r} · ${r.from} to ${r.to} · ${r.n} sessions"></div>`).join('');
 // month/year ticks along the axis
 let ticks='',cum=0,lastLbl='';
 R.forEach(r=>{const dt=parseDMY(r.from);const lbl=dt.toLocaleDateString('en-GB',{month:'short',year:'2-digit'});
  const mid=(cum+r.n/2)/tot*100;
  if(lbl!==lastLbl){ticks+=`<span style="position:absolute;left:${((cum)/tot*100).toFixed(1)}%;transform:translateX(-1px)">${lbl}</span>`;lastLbl=lbl;}
  cum+=r.n;});
 const cur=RUNS[RUNS.length-1];
 const longest=RUNS.slice().sort((a,b)=>b.n-a.n)[0];
 const tbl=RUNS.slice().reverse().slice(0,14).map(r=>
  `<tr><td style="color:${RC[r.r]};font-weight:700">${r.r}</td><td>${r.from} &rarr; ${r.to}</td><td class="n">${r.n} sess</td></tr>`).join('');
 document.getElementById('p-regime').innerHTML=`
 <div style="margin-bottom:7px"><select id="rtw">
  <option value="120">6 months</option><option value="250">1 year</option>
  <option value="520">2 years</option><option value="0">All history</option></select></div>
 <div class="obs"><b>Key observations</b>
  <span>Now: ${cur.r}, ${cur.n} sessions</span>
  <span>Longest run: ${longest.r}, ${longest.n} sess</span>
  <span>${RUNS.length} changes / ${RUNS.reduce((s,r)=>s+r.n,0)} sessions</span></div>
 <div class="card"><h3>Regime timeline &mdash; All NSE, oldest left</h3><div class="rt">${bar}</div>
  <div class="rtx">${ticks}</div>
  <div class="rl">${Object.keys(RCOL).filter(k=>k!=='n/a').map(k=>`<span><i style="background:${RCOL[k]}"></i>${k}</span>`).join('')}</div>
  <div class="cap">Each block is a continuous run at one regime, width proportional to length. Month labels below the bar.
   Short alternating blocks: chopping market, signals unreliable. Long blocks: a trend worth positioning behind.</div></div>
 <div class="card"><h3>Recent regime runs</h3><table class="runs">${tbl}</table></div>`;
 const sel=document.getElementById('rtw');if(sel){sel.value=String(RTW);sel.onchange=e=>{RTW=+e.target.value;regimePane()}}}

/* ---- scanner ---- */
function scannerPane(){const d=DATA[U],iso=d.rows[0][ISO_],L=LISTS[iso]&&LISTS[iso][U];
 const el=document.getElementById('p-scanner');
 if(!L){el.innerHTML=`<div class="card"><h3>Momentum scanner</h3><div class="cap">Stock lists for ${iso} are not bundled in this file.
  Lists are kept for the last 90 sessions in the repo and load on demand when you click a table cell.</div></div>`;return}
 const S=k=>new Set(L[k]||[]);
 const up4=S('up4'),hi=S('hi52'),u25=S('up25'),u25q=S('up25q'),u20=S('up20_5d'),ext=S('ext50');
 const score={};const add=(s,w,tag)=>s.forEach(x=>{score[x]=score[x]||{n:0,t:[]};score[x].n+=w;score[x].t.push(tag)});
 add(up4,1,'4%');add(hi,2,'52wH');add(u25,1,'25%m');add(u25q,1,'25%q');add(u20,2,'20%5d');add(ext,1,'ext');
 const hits=Object.entries(score).filter(([,v])=>v.n>=3).sort((a,b)=>b[1].n-a[1].n).slice(0,60);
 el.innerHTML=`<div class="obs"><b>Key observations</b>
  <span>${hits.length} names clear the multi-signal filter on ${iso}</span>
  <span>Universe: ${ULBL[U]||U}</span>
  <span>Ranked by how many bullish screens each name appears in</span></div>
 <div class="card"><h3>Momentum scanner &mdash; names appearing in three or more bullish screens</h3>
  ${hits.length?`<div class="sc">${hits.map(([s,v])=>`<div class="si"><b>${s}</b><span>${v.t.join(' · ')}</span></div>`).join('')}</div>`
   :'<div class="cap">No names clear the filter today. That is itself information: momentum is not concentrating.</div>'}
  <div class="cap"><b style="color:#aab8c2">How to use.</b> This is a starting list, not a buy list. A name here is moving hard and is
   confirmed across several timeframes at once, which is where continuation is most likely. Weight 52-week highs and 20%-in-5-days
   most heavily; they are the scarcest signals. Then check each name yourself for liquidity, whether the move is news-driven or
   technical, and where the stop would sit. Names carrying the "ext" tag are already more than 15% above their 50 DMA, so entries
   there carry higher drawdown risk. Cross-check against the Regime tab: this filter works in broad uptrends and produces
   false positives in distribution.</div></div>`}

/* ---- guide ---- */
function verdict(A,greens,reds){
 const r=A.regime;
 if(r==='Aggressive')return['PRESS','#3f9a63','Full size. Buy breakouts in leading sectors.'];
 if(r==='Recovery watch')return['SCALE IN','#3f7a8a','Washout turning up. Add as thrust confirms.'];
 if(r==='Stand aside')return['STAND ASIDE','#c2503c','No new longs. Protect capital.'];
 if(r==='Defensive')return['TRIM','#d8875a','Half size. Tighten stops. Leaders only.'];
 // Normal: tilt by checklist
 if(greens>=reds*2)return['HOLD, LEAN LONG','#5b8f3f','Standard size. Selective new risk on greens.'];
 if(reds>greens)return['HOLD, DEFENSIVE','#d8875a','Standard-to-half size. Wait for confirmation.'];
 return['HOLD','#9c9a30','Standard size. Be selective.'];}
function regimeBadge(){const A=ACTIONS;const b=document.getElementById('rbadge');if(!b||!A)return;
 b.textContent=A.regime;b.style.background=RCOL[A.regime]||'#3a444d';
 b.style.color=(A.regime==='Normal'||A.regime==='Defensive')?'#1a1205':'#08110c';}
function refreshReminder(){const F=(typeof FW!=='undefined')?FW:{};
 if(!F.built)return `<div class="rmd"><b>Framework data not loaded.</b> Upload your Screener CSVs to <code>frameworks/</code> in the repo. See the refresh checklist in Reference.</div>`;
 const built=new Date(F.built),days=Math.floor((Date.now()-built)/86400000);
 const ss=F.superstar_present?'':' &middot; superstar list not yet added (placeholder)';
 if(days>=90)return `<div class="rmd on"><b>&#9888; Framework data is ${days} days old.</b> Refresh your Screener exports (and superstar list) &mdash; fundamentals update each results season. See the checklist in Reference.${ss}</div>`;
 if(!F.superstar_present)return `<div class="rmd soft">Framework data ${days}d old &middot; superstar list not yet added (placeholder, optional).</div>`;
 return '';}
function changesStrip(){const C=(typeof CHANGES!=='undefined')?CHANGES:{};
 if(!C.changes||!C.changes.length)return `<div class="chgstrip quiet"><span class="cl">What changed</span><span style="color:var(--dim)">Quiet since ${C.prev_asof||'last run'} \u2014 nothing material moved</span></div>`;
 const bk={invest:'#6f9fd8',trade:'#d8b34a',both:'#6fd39a'};
 return `<div class="chgstrip"><span class="cl">What changed</span>${C.changes.map(c=>
  `<span class="chip2" style="border-color:${bk[c.book]||'#555'}"><b style="color:${bk[c.book]||'#999'}">${c.tag}</b> ${c.msg}</span>`).join('')}</div>`;}
function insightsPanel(A,greens,reds){
 // succinct cross-book read, keyword style, auto-generated
 const T=(typeof TRADER!=='undefined')?TRADER:{},v=(T.index&&T.index.vix)||{};
 const nifty=(T.index&&T.index.indices||[]).find(i=>i.tag==='NIFTY50')||{};
 const pr=DATA[A.primary==='Liquid'?'LIQUID':'ALL'],row=pr?pr.rows[0]:null;
 const a10=row?gv(row,'pct_above_10dma'):null,a50=row?gv(row,'pct_above_50dma'):null;
 const inv=[],trd=[],cross=[];
 // investing keywords
 if(a50!=null)inv.push(`50 DMA ${a50.toFixed(0)}%`);
 if(a10!=null&&a50!=null){const g=a10-a50;inv.push(g<-10?`short-term soft (${g.toFixed(0)})`:g>10?`short-term hot (+${g.toFixed(0)})`:'short-term neutral');}
 inv.push(A.regime==='Aggressive'?'press':A.regime==='Defensive'||A.regime==='Stand aside'?'defend':'selective');
 // trading keywords
 if(v.ivrank!=null)trd.push(v.ivrank<=30?`options cheap (IVR ${v.ivrank})`:v.ivrank>=70?`options rich (IVR ${v.ivrank})`:`vol mid (IVR ${v.ivrank})`);
 if(nifty.state)trd.push(`Nifty ${nifty.state}`);
 if(v.exp_move_1w_pts)trd.push(`\u00b11wk \u2248${v.exp_move_1w_pts} pts`);
 const fired=(T.fno&&T.fno.fired)||[];if(fired.length)trd.push(`${fired.length} coil fires`);
 // cross-book synthesis
 const weakening=a10!=null&&a50!=null&&(a10<a50-8);
 const cheapCoil=v.ivrank!=null&&v.ivrank<=30&&nifty.state==='squeeze';
 if(weakening&&cheapCoil)cross.push('Breadth leaking + cheap vol + Nifty coiled \u2014 calm before a move. Invest: hold/trim. Trade: own optionality, watch fires.');
 else if(cheapCoil)cross.push('Cheap vol + Nifty coiled \u2014 expansion setup loading. Own optionality over selling premium.');
 else if(A.regime==='Aggressive'&&v.ivrank>=70)cross.push('Strong breadth + rich vol \u2014 favour selling premium into strength.');
 return `<div class="insights">
  <div class="ins"><span class="il" style="color:#6f9fd8">INVEST</span> ${inv.join(' \u00b7 ')}</div>
  <div class="ins"><span class="il" style="color:#d8b34a">TRADE</span> ${trd.join(' \u00b7 ')}</div>
  ${cross.length?`<div class="ins cross"><span class="il" style="color:#6fd39a">READ</span> ${cross.join(' ')}</div>`:''}
 </div>`;}
function todayPane(){const A=ACTIONS;const el=document.getElementById('p-today');
 if(!A||!A.checks){el.innerHTML='<div class="card"><h3>Today</h3><div class="cap">No data.</div></div>';return}
 const dot=s=>`<span style="display:inline-block;width:9px;height:9px;border-radius:50%;background:${s==='green'?'#3f9a63':s==='amber'?'#d8b34a':'#c2503c'};margin-right:7px;vertical-align:0"></span>`;
 const checks=A.checks.map(c=>`<tr><td style="width:22px">${dot(c.s)}</td><td style="font-weight:600">${c.k}</td><td style="color:#aab8c2">${c.d}</td></tr>`).join('');
 const greens=A.checks.filter(c=>c.s==='green').length,reds=A.checks.filter(c=>c.s==='red').length;
 const lean=reds>greens?'risk-off, protect capital':greens>=reds*2?'constructive, press advantage':'mixed, stay selective';
 let rot='';
 if(A.rotation&&A.rotation.lead){
  rot=`<div class="card"><h3>Sector rotation, where to fish</h3>
   <div class="cr"><span class="cn" style="color:#5cc287">Leading</span><span style="color:#aab8c2">${A.rotation.lead.map(x=>x[0]+' '+x[1].toFixed(0)+'%').join(' &middot; ')}</span></div>
   <div class="cr"><span class="cn" style="color:#e07a63">Lagging</span><span style="color:#aab8c2">${A.rotation.lag.map(x=>x[0]+' '+x[1].toFixed(0)+'%').join(' &middot; ')}</span></div>
   <div class="cap">Momentum setups work best in leading sectors. Avoid fighting the laggards even with a good stock thesis; breadth is against you there.</div></div>`;}
 const ex=A.extremes&&A.extremes.length?`<div class="note">${A.extremes.join('<br>')}</div>`:'';
 const [vw,vc,vs]=verdict(A,greens,reds);
 // live flags from latest primary row
 const pr=DATA[A.primary==='Liquid'?'LIQUID':'ALL'];
 const flags=pr?pr.rows[0][F_]:[];
 const flagHtml=flags.length?flags.map(f=>`<span class="fl ${f.split(' ')[0]}">${f}</span>`).join(' '):'<span style="color:var(--dim)">none</span>';
 // crossovers
 const ups=(typeof CROSS!=='undefined'?CROSS:[]).filter(c=>c.dir==='up'),dns=(typeof CROSS!=='undefined'?CROSS:[]).filter(c=>c.dir==='dn');
 el.innerHTML=`
 <div class="verdict" style="border-color:${vc}">
  <div class="vleft"><div class="vlabel" style="color:${vc}">${vw}</div>
   <div class="vsub">${vs}</div></div>
  <div class="vright"><div class="vreg" style="color:${RCOL[A.regime]}">${A.regime}</div>
   <div class="vmeta">${A.primary} &middot; ${greens} green / ${reds} red checks</div></div></div>
 ${refreshReminder()}
 ${changesStrip()}
 ${insightsPanel(A,greens,reds)}
 ${ex}
 <div class="grid2">
  <div class="card"><h3>Checklist</h3><table class="runs"><tbody>${checks}</tbody></table>
   <div class="cap" style="margin-top:6px">Mechanical read, not a recommendation. <a class="jl" onclick="jump('guide')">what do these mean? &rarr;</a></div></div>
  <div class="card"><h3>Sector rotation &amp; flags</h3>
   ${A.rotation&&A.rotation.lead?`<div class="cr"><span class="cn" style="color:#5cc287">Fish here</span><span style="color:#aab8c2">${A.rotation.lead.map(x=>x[0]+' '+x[1].toFixed(0)+'%').join(' &middot; ')}</span></div>
   <div class="cr"><span class="cn" style="color:#e07a63">Avoid</span><span style="color:#aab8c2">${A.rotation.lag.map(x=>x[0]+' '+x[1].toFixed(0)+'%').join(' &middot; ')}</span></div>`:''}
   ${ups.length?`<div class="cr"><span class="cn" style="color:#5cc287">Turning up</span><span style="color:#aab8c2">${ups.map(c=>c.label).join(', ')}</span></div>`:''}
   ${dns.length?`<div class="cr"><span class="cn" style="color:#e07a63">Rolling over</span><span style="color:#aab8c2">${dns.map(c=>c.label).join(', ')}</span></div>`:''}
   <div class="cr"><span class="cn">Live flags</span><span>${flagHtml}</span></div>
   ${(typeof STOCKS!=='undefined'&&STOCKS.n_strict)?`<div class="cr"><span class="cn" style="color:#6fd39a">Stage-2 names</span><span style="color:#aab8c2">${STOCKS.n_strict} pass strict template &nbsp;<a class="jl" onclick="jump('screen')">screen &rarr;</a></span></div>`:''}
   <div class="cap" style="margin-top:6px"><a class="jl" onclick="jump('sectors')">full sector view &rarr;</a> &nbsp; <a class="jl" onclick="jump('scanner')">momentum scanner &rarr;</a></div></div>
 </div>
 <div class="gs"><h4>How serious Indian breadth traders act on this</h4>
  <table><tbody>
   <tr><td>Regime sets exposure</td><td>Aggressive full, Normal standard, Defensive half, Stand aside cash &nbsp;<a class="jl" onclick="jump('regime')">Regime &rarr;</a></td></tr>
   <tr><td>Only add on green checklist</td><td>Three or more greens before pressing new risk</td></tr>
   <tr><td>Fish in leading sectors</td><td>Breakouts where sector breadth &gt; 60% &nbsp;<a class="jl" onclick="jump('sectors')">Sectors &rarr;</a></td></tr>
   <tr><td>Trim into froth</td><td>Ext &gt; 35% or 50%/month &gt; 20: scale out &nbsp;<a class="jl" onclick="jump('table')">Table &rarr;</a></td></tr>
   <tr><td>Buy washouts, not tops</td><td>Act on defensive extremes and Zweig thrusts &nbsp;<a class="jl" onclick="jump('charts')">Charts &rarr;</a> <a class="jl" onclick="jump('guide')">what&rsquo;s a defensive extreme? &rarr;</a></td></tr>
   <tr><td>Divergence tightens stops</td><td>Bear div is not a sell: raise stops on open longs</td></tr>
  </tbody></table></div>`;}

function referencePane(){document.getElementById('p-reference').innerHTML=`
 <div class="gs" style="grid-column:1/-1;border-color:#a07a30"><h4 style="color:#e0c68a">Framework refresh checklist &mdash; every quarter</h4>
  <dl><dt>Why quarterly</dt><dd>The Playbook frameworks are fundamental screens (ROCE, EPS growth, Piotroski, cash conversion). Fundamentals only move when results are declared, which is quarterly in India. So refresh a few weeks after each results season, once reporting is largely complete: <b>mid-Feb</b> (Q3), <b>mid-May</b> (Q4/annual, the most important), <b>mid-Aug</b> (Q1), <b>mid-Nov</b> (Q2). The dashboard shows a red reminder once the data passes 90 days.</dd>
  <dt>How to refresh</dt><dd>1. In Screener, re-run each of your 8 saved screens and export the CSV. 2. In the repo, open <code>frameworks/</code>, click each file, pencil icon, select-all, delete, paste the new CSV, commit. Same method you used to upload them. 3. The bridge updates on the next pipeline run; the red reminder clears automatically.</dd>
  <dt>The 8 frameworks</dt><dd>Coffee Can, Consistent Compounder, GARP, Cash-is-King, Peter Lynch, Vijay Malik, Piotroski, 100-Bagger.</dd>
  <dt>Superstar list (placeholder)</dt><dd>Not yet active. When the superstar-tracker can export a symbol list, save it as <code>frameworks/superstar.csv</code> with an <code>NSE Code</code> column and it maps automatically alongside the others, on the same quarterly cadence. Until then this slot is a documented placeholder.</dd>
  <dt>Keep the header row</dt><dd>Each CSV must keep its <code>NSE Code</code> column header. The bridge reads that column; nothing else in the file matters to it.</dd></dl></div>
 <div class="gs"><h4>Primary sources on this methodology</h4><table class="two"><tbody>
  <tr><td>Stockbee Market Monitor page</td><td>stockbee.blogspot.com/p/mm.html</td></tr>
  <tr><td>Using breadth to avoid crashes (2011)</td><td>stockbee.blogspot.com/2011/08/how-to-use-market-breadth-to-avoid.html</td></tr>
  <tr><td>Open-source implementation, backtested regimes</td><td>github.com/dcimring/stockbee-dashboard</td></tr>
  <tr><td>Nitin R, Chartink build (your workbook&rsquo;s origin)</td><td>finallynitin.substack.com/p/stockbee-market-monitor</td></tr>
  <tr><td>Bonde on edge (video)</td><td>Investors Underground, &ldquo;How to Find Your Edge with Pradeep Bonde&rdquo;</td></tr>
  <tr><td>Martin Zweig, Breadth Thrust (book)</td><td>Winning on Wall Street</td></tr>
  <tr><td>Stan Weinstein, 30-week stage analysis</td><td>Secrets for Profiting in Bull and Bear Markets</td></tr>
 </tbody></table></div>
 <div class="gs"><h4>The single most important idea</h4>
  <dl><dd>Breadth is most useful at extremes and close to noise between them. The 5-day ratio reaching an extreme is the actionable event, not the day-to-day wiggle. There is an asymmetry worth burning in: extremely bearish breadth is a reliable bullish signal, while extremely bullish breadth has a poor record of calling tops, because tops are gradual and bottoms are violent. Use breadth to add risk after washouts and to trim risk gradually, never to time exits precisely.</dd></dl></div>
 <div class="gs"><h4>India calibration, measured on this store</h4><table class="two"><tbody>
  <tr><td>Daily sigma (all NSE)</td><td>2.82%</td></tr>
  <tr><td>4% up movers, median day</td><td>4.9% of universe</td></tr>
  <tr><td>Bonde&rsquo;s US thrust bar</td><td>4.2% &mdash; below the Indian median</td></tr>
  <tr><td>India daily tier added</td><td>6% (fires 2.6% of days)</td></tr>
  <tr><td>25%/month</td><td>transfers well (2.3%)</td></tr>
  <tr><td>50%/month</td><td>transfers almost exactly (0.26% vs US 0.28%)</td></tr>
  <tr><td>25%/quarter (US)</td><td>over-fires in India (5.6%)</td></tr>
  <tr><td>India quarter tier added</td><td>35%/65d (3.1%)</td></tr>
 </tbody></table>
  <div class="cap">Thresholds calibrated on 589 sessions from Apr 2024, a predominantly rising sample. They will drift as a full correction enters the record; treat the ratio extremes as provisional until then.</div></div>
 <div class="gs"><h4>Momentum school: how breakout traders use this</h4>
  <dl><dt>Qullamaggie, the market filter</dt><dd>His only top-down rule: when the 10-day and 20-day are sloping down and breakouts keep failing, go to cash or trade small. On this dashboard that is the % above 10 DMA and 20 DMA lines rolling over together, and the Net 4% bars turning persistently red. He does not trade breakouts into weak breadth.</dd>
  <dt>Where he fishes</dt><dd>Only in leading groups. The Sectors tab, ranked by 10 and 20 DMA, is that filter. He buys the top 1-2% of performers surfing their 10/20 DMA, never below the 50 DMA, so a name in the Scanner tab tagged 52wH and up-in-5d, in a top-3 sector, is his archetype.</dd>
  <dt>Episodic Pivots</dt><dd>Gap-ups above 10% on heavy volume after a catalyst. Your U10 column is the daily count of these; a rising U10 in a strong regime means EP setups are firing across the market.</dd>
  <dt>Minervini, breadth confirmation</dt><dd>His Stage-2 template wants the broad market in a confirmed uptrend before pressing risk, and stacked moving averages (50 above 150 above 200) on individual names. The MA structure columns are the market-wide version of that stacking check.</dd>
  <dt>Weinstein, the 30-week line</dt><dd>Stage analysis turns on the 30-week EMA. Not yet in the dashboard; flagged as the next addition. It would sit between the 50 and 200 DMA reads as the true intermediate stage gauge.</dd></dl></div>`;}

function guidePane(){document.getElementById('p-guide').innerHTML=`
 <div class="note"><b>Use breadth at extremes, not in the middle.</b> The 5-day ratio hitting an extreme is the event to act on. Extremely bearish breadth reliably marks bottoms; extremely bullish breadth does not reliably mark tops. Add risk after washouts, trim risk gradually.</div>
 <div class="gd">
 <div class="gs" style="grid-column:1/-1"><h4>Key terms, with a worked example each</h4><table><tbody>
  <tr><td>T2108</td><td>Percent of stocks above their 40-day moving average. The oldest breadth gauge, from Worden. <b>Example:</b> T2108 = 47 means 47% of the universe is above its 40 DMA, a middling tape. Below 20 is oversold, above 80 is overbought. It leads the 50 DMA reading slightly because 40 &lt; 50.</td></tr>
  <tr><td>Defensive extreme</td><td>The 5-day ratio at or below 0.5, meaning 4% decliners outnumbered 4% gainers roughly 2:1 or worse over the week. <b>Example:</b> a ratio of 0.45 after a three-week slide has historically sat within days of an intermediate low. It is a signal to prepare to buy, not to sell.</td></tr>
  <tr><td>Aggressive extreme</td><td>The 5-day ratio at or above 5.0, India-calibrated (US uses 2.0). <b>Example:</b> a ratio of 6 after a washout confirms a thrust and says press long exposure. The same reading late in an extended run means less.</td></tr>
  <tr><td>5-day ratio</td><td>Sum of the last five sessions&rsquo; 4%-up counts divided by the sum of 4%-down counts. <b>Example:</b> 90 up-4% and 30 down-4% over the week gives 3.0, firmly bullish but short of the 5.0 extreme.</td></tr>
  <tr><td>Divergence (bear)</td><td>Nifty prints a fresh 20-session high while % above 50 DMA does not. <b>Example:</b> index at a new high but 50 DMA breadth stuck at 52 versus 60 a month ago means fewer stocks carry the tape; raise stops.</td></tr>
  <tr><td>Zweig thrust</td><td>The 10-day advance ratio races from below 0.40 to above 0.615 within ten sessions. <b>Example:</b> it has fired only nine times in this record; each marked the start of a strong multi-week advance. Rare and unambiguous.</td></tr>
  <tr><td>Crossover (10&times;50)</td><td>A sector&rsquo;s % above 10 DMA crossing above its % above 50 DMA. <b>Example:</b> Infra 10 DMA rising through its 50 DMA line is the first sign a laggard is turning up, before the 50 DMA itself improves. The Sectors tab flags these.</td></tr>
 </tbody></table></div>
 <div class="gs"><h4>Regime, action labels</h4><table><tbody>
  <tr><td style="color:#2c8f57;font-weight:700">Aggressive</td><td>&ge;58% above 50 DMA, ratio &ge;1</td><td>Full size, buy breakouts freely</td></tr>
  <tr><td style="color:#5b8f3f;font-weight:700">Normal</td><td>45&ndash;58% above 50 DMA</td><td>Standard size, be selective</td></tr>
  <tr><td style="color:#9c7a30;font-weight:700">Defensive</td><td>25&ndash;45% above 50 DMA</td><td>Half size, tighten stops, leaders only</td></tr>
  <tr><td style="color:#8f3a2c;font-weight:700">Stand aside</td><td>&lt;25%, no upturn</td><td>No new longs, protect capital</td></tr>
  <tr><td style="color:#3f7a8a;font-weight:700">Recovery watch</td><td>&lt;12% but 5d ratio turning up</td><td>Scale in as thrust confirms</td></tr>
 </tbody></table></div>
 <div class="gs"><h4>India-calibrated ratio thresholds</h4><table class="two"><tbody>
  <tr><td>5-day ratio, aggressive extreme</td><td>&ge; 5.0</td></tr>
  <tr><td>5-day ratio, neutral band</td><td>0.9 &ndash; 3.1</td></tr>
  <tr><td>5-day ratio, defensive extreme</td><td>&le; 0.5</td></tr>
  <tr><td>10-day ratio, aggressive</td><td>&ge; 3.5</td></tr>
  <tr><td>10-day ratio, defensive</td><td>&le; 0.7</td></tr>
 </tbody></table>
  <div class="cap">Bonde&rsquo;s US thresholds (2.0 / 0.5) do not transfer. The Indian 5-day median is 1.7 on a bull-heavy sample.</div></div>
 <div class="gs"><h4>Bonde scaled count signals</h4><table class="two"><tbody>
  <tr><td>Breadth thrust (fund buying)</td><td>~110 stocks up 4% on All NSE</td></tr>
  <tr><td>Correction developing</td><td>~255 stocks down 4%, repeated</td></tr>
  <tr><td>Seller capitulation</td><td>25%/quarter count very low</td></tr>
  <tr><td>Top warning</td><td>&gt;20 stocks up 50% in a month</td></tr>
 </tbody></table></div>
 <div class="gs"><h4>Flags</h4><table><tbody>
  <tr><td style="font-weight:700">ZWEIG</td><td>10-day advance ratio crosses 0.40 to 0.615 within 10 sessions. Rare, historically precedes major advances.</td></tr>
  <tr><td style="font-weight:700">THRUST</td><td>&ge;10% of universe up 4%, and &ge;3&times; the down-4% count. Durable-low signature after declines.</td></tr>
  <tr><td style="font-weight:700">BEAR DIV</td><td>Nifty 20-day high, % above 50 DMA not confirming. Raise stops, not a sell.</td></tr>
  <tr><td style="font-weight:700">BULL DIV</td><td>Nifty 20-day low, % above 50 DMA not confirming. The more reliable of the two.</td></tr>
 </tbody></table></div>
 <div class="gs"><h4>Column glossary</h4><table><tbody>
  <tr><td>% &gt;10/20/50/200 DMA</td><td>Share above that SMA. 50 is the regime gauge.</td></tr>
  <tr><td>T2108</td><td>% above 40 DMA. Bonde&rsquo;s classic intermediate gauge.</td></tr>
  <tr><td>Ext</td><td>% more than 15% above 50 DMA. Froth, not strength. &gt;30 is stretched.</td></tr>
  <tr><td>10&gt;20, 20&gt;50, 50&gt;200</td><td>MA stacking. Cleaner than raw percent-above.</td></tr>
  <tr><td>52wH/L, H/L</td><td>New highs, lows, and highs/(highs+lows). &gt;0.85 broad strength.</td></tr>
  <tr><td>U4/D4/Net4</td><td>Up or down 4%+, close to close. The daily pulse.</td></tr>
  <tr><td>U6/D6</td><td>6%+ tier, India-calibrated to match Bonde&rsquo;s US 4%.</td></tr>
  <tr><td>U10/D10</td><td>10%+, genuine dislocation.</td></tr>
  <tr><td>R5/R10</td><td>Stockbee ratios: 5 or 10 sessions of 4% up divided by 4% down.</td></tr>
  <tr><td>U25m/U50m</td><td>25% and 50% moves over 21 sessions. 50m&gt;20 is a top warning.</td></tr>
  <tr><td>U25q/U35q</td><td>25% (US) and 35% (India tier) over 65 sessions. Regime-defining.</td></tr>
  <tr><td>U20w</td><td>20%+ in 5 sessions. Momentum burst.</td></tr>
 </tbody></table></div>
 <div class="gs"><h4>Colour logic</h4>
  <dl><dd>Fixed absolute thresholds, never percentiles. Percentage columns graded 0&ndash;100 with tighter transitions across the 42&ndash;58 regime boundary. Counts converted to a share of the day&rsquo;s universe first, so a number means the same as the universe grows. Down-columns inverted, so red always reads bearish. Long mid-tone stretches are the truth, not a fault.</dd></dl></div>
 <div class="gs"><h4>Regime timeline, how to read it</h4>
  <dl><dd>Each block is a continuous run at one regime, width proportional to length. Long blocks mean a trend worth positioning behind; rapid alternation means a chopping market where signals whipsaw, so cut size and shorten holds. Fast moves from Aggressive to Defensive tend to resolve back up; slow grinds tend not to.</dd></dl></div>
 </div>`}

/* ---- stock list overlay ---- */
async function show(iso,k){const o=document.getElementById('ov');
 document.getElementById('bh').textContent=`${ULBL[U]||U} · ${LBL[k]||k}`;
 document.getElementById('bs').textContent=iso;document.getElementById('by').textContent='loading...';
 o.classList.add('on');
 let L=LISTS[iso];
 if(!L&&REPO){try{const r=await fetch(`https://raw.githubusercontent.com/${REPO}/main/data/lists/${iso}.json`);
  if(r.ok){L=await r.json();LISTS[iso]=L}}catch(e){}}
 const s=L&&L[U]&&L[U][k];
 document.getElementById('by').textContent=s&&s.length?s.join('   ')
  :'Not available. Symbol lists are kept for the last 90 sessions.';
 document.getElementById('bs').textContent=`${iso}${s?' · '+s.length+' stocks':''}`}

/* ---- draw ---- */
function jump(t){selectTab(t);}
function draw(){const d=DATA[U];
 regimeBadge();
 if(TAB==='today')return todayPane();
 if(TAB==='screen')return screenPane();
 if(TAB==='trader')return traderPane();
 if(TAB==='reference')return referencePane();
 if(TAB==='guide')return guidePane();
 if(TAB==='regime')return regimePane();
 if(TAB==='segments')return comparePane();
 if(TAB==='sectors')return sectorPane();
 if(!d)return;
 if(TAB==='table'){pills(d);bars(d);obsP(d);body(d)}
 else if(TAB==='charts')chartPane();
 else if(TAB==='scanner')scannerPane()}
document.getElementById('rows').onchange=e=>{N=+e.target.value;body(DATA[U])};
document.getElementById('ov').onclick=e=>{if(e.target.id==='ov')e.currentTarget.classList.remove('on')};
document.getElementById('ft').innerHTML='Source: NSE UDiFF bhavcopy, series EQ. Prices chained on close over previous close, so splits and bonuses are adjusted. F&amp;O and index segments are point-in-time from NSE constituent files. Thresholds are calibrated on this dashboard&rsquo;s own Indian history, not imported from US studies. Research tooling, not investment advice.';
tabs();usel();head();draw();
try{const c=document.getElementById('gc'),x=c&&c.getContext&&c.getContext('2d');
 if(x)for(let i=0;i<150;i++){x.fillStyle=clr(i/149);x.fillRect(i,0,1,10)}}catch(e){}
</script></body></html>"""


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", default="data/breadth_history.csv")
    ap.add_argument("--out", default="dashboard.html")
    ap.add_argument("--rows", type=int, default=250)
    ap.add_argument("--repo", default="bobbythomas-create/market-breadth")
    a = ap.parse_args()
    build(a.csv, a.out, a.rows, a.repo)
