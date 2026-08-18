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
                            ("pct_above_50dma", "50", None), ("pct_above_200dma", "200", None),
                            ("pct_extended_50dma", "Ext", "ext50")]),
    ("MA structure", [("pct_10dma_gt_20dma", "10>20", None), ("pct_20dma_gt_50dma", "20>50", None),
                      ("pct_50dma_gt_200dma", "50>200", None)]),
    ("Extremes", [("new_52w_high", "52wH", "hi52"), ("new_52w_low", "52wL", "lo52"),
                  ("hl_ratio", "H/L", None)]),
    ("Nifty", [("nifty_close", "Close", None), ("nifty_chg_pct", "Chg%", None)]),
    ("Breadth", [("advances", "Adv", None), ("declines", "Dec", None)]),
    ("Daily momentum", [("up_4pct", "U4", "up4"), ("down_4pct", "D4", "dn4"),
                        ("net_4pct", "Net4", None), ("up_10pct", "U10", "up10"),
                        ("down_10pct", "D10", "dn10"),
                        ("ratio_5d", "R5", None), ("ratio_10d", "R10", None)]),
    ("Range movers", [("up_25pct_21d", "U25m", "up25"), ("down_25pct_21d", "D25m", "dn25"),
                      ("up_25pct_63d", "U25q", "up25q"), ("down_25pct_63d", "D25q", "dn25q"),
                      ("up_20pct_5d", "U20w", "up20_5d"), ("down_20pct_5d", "D20w", "dn20_5d")]),
]
COLS = [(k, l, lk) for _, cs in GROUPS for k, l, lk in cs]
PCT_FMT = {k for k, _, _ in COLS if k.startswith("pct_")}
NARROW = {"up_4pct", "down_4pct", "net_4pct", "up_10pct", "down_10pct", "ratio_5d", "ratio_10d",
          "up_25pct_21d", "down_25pct_21d", "up_25pct_63d", "down_25pct_63d",
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

INVERTED = {"declines", "down_4pct", "down_10pct", "down_25pct_21d", "down_25pct_63d",
            "down_20pct_5d", "new_52w_low"}

SECTORS = ["SEC_BANK", "SEC_FINSRV", "SEC_IT", "SEC_PHARMA", "SEC_AUTO", "SEC_FMCG",
           "SEC_METAL", "SEC_ENERGY", "SEC_INFRA", "SEC_REALTY", "SEC_PSE", "SEC_MEDIA"]
SIZE_UNIVERSES = ["ALL", "FNO", "NIFTY50", "NIFTYNEXT50", "MIDCAP150", "SMALLCAP250", "NIFTY500"]
ULBL = {"ALL": "All NSE", "FNO": "F&O", "NIFTY50": "Nifty 50", "NIFTYNEXT50": "Next 50",
        "MIDCAP150": "Midcap 150", "SMALLCAP250": "Smallcap 250", "NIFTY500": "Nifty 500",
        "SEC_BANK": "Bank", "SEC_FINSRV": "Fin Services", "SEC_IT": "IT", "SEC_PHARMA": "Pharma",
        "SEC_AUTO": "Auto", "SEC_FMCG": "FMCG", "SEC_METAL": "Metal", "SEC_ENERGY": "Energy",
        "SEC_INFRA": "Infra", "SEC_REALTY": "Realty", "SEC_PSE": "PSE", "SEC_MEDIA": "Media"}
WIDE_SET = {"ALL", "NIFTY500", "SMALLCAP250"}


def _interp(anchors, v):
    return float(np.clip(np.interp(v, [a[0] for a in anchors], [a[1] for a in anchors]), 0, 1))


def shade(universe, key, value, n):
    if value is None or (isinstance(value, float) and np.isnan(value)):
        return None
    if key == "nifty_close":
        return None
    if key.startswith("pct_"):
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
    anchors = prof.get(key)
    if not anchors or not n:
        return None
    s = _interp(anchors, value / n * 100)
    return round(1.0 - s if key in INVERTED else s, 2)


def regime(row):
    a50 = row.get("pct_above_50dma", np.nan)
    if pd.isna(a50):
        return "insufficient data"
    if a50 >= 60:
        return "broad uptrend"
    if a50 >= 45:
        return "narrowing uptrend"
    if a50 >= 25:
        return "corrective, mixed"
    if a50 >= 12:
        return "distribution"
    return "washed out"


REGIME_TONE = {"broad uptrend": 0.88, "narrowing uptrend": 0.62, "corrective, mixed": 0.4,
               "distribution": 0.18, "washed out": 0.04, "insufficient data": 0.5}


def observations(g, universe):
    obs = []
    if len(g) < 12:
        return obs
    last = g.iloc[-1]
    a50 = g["pct_above_50dma"].values
    a10 = g["pct_above_10dma"].values
    a200 = g["pct_above_200dma"].values

    d5 = a50[-5:]
    if len(d5) == 5 and not np.isnan(d5).any():
        if all(d5[i] >= d5[i - 1] for i in range(1, 5)):
            obs.append(f"% above 50 DMA rising five straight sessions, {d5[0]:.0f} to {d5[-1]:.0f}")
        elif all(d5[i] <= d5[i - 1] for i in range(1, 5)):
            obs.append(f"% above 50 DMA falling five straight sessions, {d5[0]:.0f} to {d5[-1]:.0f}")

    if not np.isnan(a10[-1]) and not np.isnan(a50[-1]):
        gap = a10[-1] - a50[-1]
        if gap > 15:
            obs.append(f"Short term running {gap:.0f} pts ahead of intermediate, stretched")
        elif gap < -12:
            obs.append(f"Short term {abs(gap):.0f} pts behind intermediate, near-term selling into a firm trend")

    r5 = last.get("ratio_5d", np.nan)
    if not pd.isna(r5):
        if r5 >= 5.0:
            obs.append(f"5-day ratio {r5:.1f}, aggressive extreme by India calibration")
        elif r5 <= 0.5:
            obs.append(f"5-day ratio {r5:.2f}, defensive extreme, historically near lows")

    if not pd.isna(a50[-1]) and not pd.isna(a200[-1]):
        if a50[-1] > 55 and a200[-1] > 55:
            obs.append("Intermediate and long-term trends aligned bullish")
        elif a50[-1] < 40 and a200[-1] < 45:
            obs.append("Intermediate and long-term trends aligned bearish")
        elif a50[-1] < 45 < a200[-1]:
            obs.append("Intermediate weakening while long-term holds, corrective phase")

    ext = last.get("pct_extended_50dma", np.nan)
    if not pd.isna(ext) and ext > 30:
        obs.append(f"{ext:.0f}% of stocks extended above 50 DMA, froth building")

    hl = last.get("hl_ratio", np.nan)
    if not pd.isna(hl):
        if hl > 0.85 and (last.get("new_52w_high", 0) + last.get("new_52w_low", 0)) > 10:
            obs.append("New highs overwhelming new lows, broad strength")
        elif hl < 0.2 and (last.get("new_52w_high", 0) + last.get("new_52w_low", 0)) > 10:
            obs.append("New lows overwhelming new highs, broad weakness")

    flags = []
    for _, r in g.tail(6).iterrows():
        d = r["date"].strftime("%d/%m")
        if r.get("thrust", False):
            flags.append(f"thrust {d}")
        if r.get("div_bearish", False):
            flags.append(f"bear div {d}")
        if r.get("div_bullish", False):
            flags.append(f"bull div {d}")
    if flags:
        obs.append("Recent signals: " + ", ".join(flags[:4]))
    return obs[:5]


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


def build(csv, out, rows, repo):
    df = pd.read_csv(csv)
    df["date"] = pd.to_datetime(df["date"])
    if "pct_20dma_gt_40dma" in df.columns and "pct_20dma_gt_50dma" not in df.columns:
        df["pct_20dma_gt_50dma"] = df["pct_20dma_gt_40dma"]
    lists = load_lists(os.path.dirname(os.path.abspath(csv)))

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
        # thin series for charts: date + 4 numbers
        sub = gg.tail(250)
        series[u] = {"d": [x.strftime("%d/%m/%y") for x in sub["date"]],
                     "a50": [None if pd.isna(v) else round(float(v), 1) for v in sub["pct_above_50dma"]],
                     "a200": [None if pd.isna(v) else round(float(v), 1) for v in sub["pct_above_200dma"]],
                     "nifty": [None if pd.isna(v) else round(float(v), 0) for v in sub["nifty_close"]],
                     "net4": [None if pd.isna(v) else int(v) for v in sub["net_4pct"]],
                     "reg": [REGIME_TONE.get(regime(r), 0.5) for _, r in sub.iterrows()]}
        if u in sizes:
            summary[u] = {"asof": last["date"].strftime("%Y-%m-%d"), "n": int(last["universe_count"]),
                          "regime": regime(last), "a50": last.get("pct_above_50dma"),
                          "a200": last.get("pct_above_200dma"), "r5": last.get("ratio_5d"),
                          "flags": recs[0][3]}

    runs = regime_runs(df[df.universe == (sizes[0] if sizes else present.pop())].sort_values("date"))

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
/* scanner */
.sc{display:grid;grid-template-columns:repeat(auto-fill,minmax(128px,1fr));gap:5px;margin-top:9px}
.si{background:#0d1216;border:1px solid var(--rule);border-radius:2px;padding:5px 8px;font:11px ui-monospace,Menlo,monospace}
.si b{display:block;color:var(--acc);font-size:11.5px}
.si span{color:var(--dim);font-size:9.5px}
/* guide */
.gd{display:grid;grid-template-columns:repeat(auto-fill,minmax(320px,1fr));gap:9px}
.gs{border:1px solid var(--rule);background:var(--pnl);border-radius:3px;padding:11px 13px}
.gs h4{margin:0 0 7px;font-size:11px;letter-spacing:.1em;text-transform:uppercase;color:var(--acc);font-weight:650}
.gs dl{margin:0;font-size:11.5px;line-height:1.6}
.gs dt{font-weight:700;color:var(--ink);margin-top:7px}
.gs dt:first-child{margin-top:0}
.gs dd{margin:1px 0 0;color:#aab8c2}
.gs table{width:100%;font-size:11px;font-family:inherit;margin-top:4px}
.gs table td{color:#aab8c2;font-weight:400;padding:2.5px 5px;border-bottom:1px solid #202931;text-align:left}
.gs table td:last-child{text-align:right;font-family:ui-monospace,Menlo,monospace;color:var(--ink)}
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
footer{margin-top:9px;color:var(--dim);font-size:10px;line-height:1.55}
@media(max-width:760px){.pill{min-width:64px}.pill.reg{min-width:100%}.ct{max-width:180px}.cn{width:72px}}
</style></head><body><div class="wrap">
<div class="top"><div><h1>Market Breadth</h1><span class="as" id="as"></span></div>
<div class="ctl" id="usel"></div></div>
<div class="tabs" id="tabs"></div>

<div class="pane on" id="p-table">
  <div class="pills" id="pills"></div><div class="bars" id="bars"></div><div class="obs" id="obs"></div>
  <div style="margin-bottom:7px"><select id="rows"><option value="60">60 sessions</option>
  <option value="120">120</option><option value="250" selected>250</option><option value="0">All</option></select></div>
  <div class="tw"><table id="t"><thead></thead><tbody></tbody></table></div>
  <div class="lg"><span>Fixed thresholds, India-calibrated</span><canvas class="gr" id="gc" width="150" height="10"></canvas>
  <span>bearish to bullish. Counts graded as share of universe</span><span>shaded cells with a hover outline open the stock list</span></div>
</div>

<div class="pane" id="p-charts"></div>
<div class="pane" id="p-sectors"></div>
<div class="pane" id="p-compare"></div>
<div class="pane" id="p-regime"></div>
<div class="pane" id="p-scanner"></div>
<div class="pane" id="p-guide"></div>

<footer id="ft"></footer></div>
<div id="ov"><div id="bx"><h3 id="bh"></h3><div class="sub" id="bs"></div><div class="sy" id="by"></div>
<button onclick="document.getElementById('ov').classList.remove('on')">Close</button></div></div>
<script>
const KEYS=__KEYS__,KI={};__KEYS__.forEach((k,i)=>KI[k]=i);
const DATA=__DATA__,SER=__SERIES__,RUNS=__RUNS__,GROUPS=__GROUPS__,NARROW=new Set(__NARROW__),
 LISTS=__LISTS__,SIZES=__SIZES__,SECTS=__SECTS__,ULBL=__ULBL__,REPO="__REPO__";
const LBL={up4:"up 4%+",dn4:"down 4%+",up10:"up 10%+",dn10:"down 10%+",hi52:"at a 52-week high",
 lo52:"at a 52-week low",up25:"up 25%+ in 21 sessions",dn25:"down 25%+ in 21 sessions",
 up25q:"up 25%+ in a quarter",dn25q:"down 25%+ in a quarter",up20_5d:"up 20%+ in 5 sessions",
 dn20_5d:"down 20%+ in 5 sessions",ext50:"more than 15% above its 50 DMA"};
let U=SIZES[0]||SECTS[0],N=250,TAB="table";
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
 e.style.display=(TAB==='compare'||TAB==='guide'||TAB==='sectors')?'none':'flex';
 e.querySelectorAll('.us').forEach(t=>t.onclick=()=>{U=t.dataset.u;usel();draw()})}
function tabs(){const T=[['table','Table'],['charts','Charts'],['sectors','Sectors'],['compare','Compare'],
 ['regime','Regime'],['scanner','Scanner'],['guide','Guide']];
 document.getElementById('tabs').innerHTML=T.map(([k,l])=>
  `<div class="tb" data-t="${k}" aria-selected="${k===TAB}">${l}</div>`).join('');
 document.querySelectorAll('.tb').forEach(t=>t.onclick=()=>{TAB=t.dataset.t;
  document.querySelectorAll('.tb').forEach(x=>x.setAttribute('aria-selected',x===t));
  document.querySelectorAll('.pane').forEach(p=>p.classList.toggle('on',p.id==='p-'+TAB));
  usel();draw()})}

/* ---- table pane ---- */
function pills(d){const r=d.rows[0],p=[];
 p.push(`<div class="pill reg"><div class="k">Regime &middot; ${d.label}</div><div class="v">${d.regime}</div></div>`);
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
function chartPane(){const s=SER[U],d=DATA[U];if(!s)return;
 const W=1000,H=170,n=s.d.length;
 const nz=s.nifty.filter(v=>v!=null),nlo=Math.min(...nz)*0.99,nhi=Math.max(...nz)*1.01;
 const bands=[[0,15,'#3a1c17'],[15,30,'#41291c'],[30,45,'#3d3520'],[45,60,'#25341f'],[60,100,'#1b3a26']];
 const bandsSvg=bands.map(([a,b,c])=>`<rect x="0" y="${H-b/100*H}" width="${W}" height="${(b-a)/100*H}" fill="${c}" opacity=".55"/>`).join('');
 const gridx=[0,.25,.5,.75,1].map(f=>`<line x1="${f*W}" y1="0" x2="${f*W}" y2="${H}" stroke="#2b353e" stroke-width=".6"/>`).join('');
 const lbl=[0,.25,.5,.75,1].map(f=>`<text x="${f*W}" y="${H+13}" fill="#7d8d99" font-size="10" text-anchor="${f?f===1?'end':'middle':'start'}">${s.d[Math.round(f*(n-1))]}</text>`).join('');
 const n4max=Math.max(1,...s.net4.map(v=>Math.abs(v||0)));
 const n4bars=s.net4.map((v,i)=>{const x=i/n*W,bw=Math.max(1.2,W/n-0.6),hh=Math.abs(v||0)/n4max*46;
  return`<rect x="${x.toFixed(1)}" y="${(v>=0?50-hh:50).toFixed(1)}" width="${bw.toFixed(1)}" height="${hh.toFixed(1)}" fill="${v>=0?'#3f9a63':'#c2503c'}"/>`}).join('');
 document.getElementById('p-charts').innerHTML=`
 <div class="obs" id="obsC"></div>
 <div class="card"><h3>Participation vs price &mdash; ${d.label}</h3>
  <svg viewBox="0 0 ${W} ${H+20}" preserveAspectRatio="none" style="height:200px">${bandsSvg}${gridx}
   ${line(s.a50,W,H,0,100,'#5cc287')}${line(s.a200,W,H,0,100,'#6f9fd8')}
   ${line(s.nifty,W,H,nlo,nhi,'#d8b34a')}${lbl}</svg>
  <div class="cap"><span style="color:#5cc287">&#9644;</span> % above 50 DMA &nbsp;
   <span style="color:#6f9fd8">&#9644;</span> % above 200 DMA &nbsp;
   <span style="color:#d8b34a">&#9644;</span> Nifty 50, rescaled.
   Background bands mark regime zones: red below 30, amber 30 to 45, green above 60.
   Where the gold line rises while the green line falls, the index is being carried by fewer stocks.</div></div>
 <div class="card"><h3>Net 4% movers</h3>
  <svg viewBox="0 0 ${W} 100" preserveAspectRatio="none" style="height:110px">
   <line x1="0" y1="50" x2="${W}" y2="50" stroke="#2b353e"/>${n4bars}</svg>
  <div class="cap">Stocks up 4% minus stocks down 4%, each session. Clusters of tall green bars after a decline
   are the thrust signature; sustained red under a flat index is distribution.</div></div>`;
 obsP(d,'obsC')}

/* ---- sectors ---- */
function sectorPane(){if(!SECTS.length){document.getElementById('p-sectors').innerHTML=
  '<div class="card"><h3>Sector breadth</h3><div class="cap">Sector data has not been ingested yet. Run the pipeline with the updated ingest script to populate the twelve NSE sectoral indices.</div></div>';return}
 const rank=SECTS.map(u=>({u,r:DATA[u]?DATA[u].rows[0]:null})).filter(x=>x.r)
  .sort((a,b)=>(gv(b.r,'pct_above_50dma')||0)-(gv(a.r,'pct_above_50dma')||0));
 const top=rank.slice(0,3).map(x=>ULBL[x.u]).join(', '),bot=rank.slice(-3).map(x=>ULBL[x.u]).join(', ');
 const rows=m=>rank.map(x=>{const v=gv(x.r,m);if(v==null)return'';
  return`<div class="cr"><span class="cn">${ULBL[x.u]}</span><div class="ct">
  <div class="cf" style="width:${v}%;background:${bc(v)}"></div></div><span class="cv">${v.toFixed(0)}%</span></div>`}).join('');
 document.getElementById('p-sectors').innerHTML=`
 <div class="obs"><b>Key observations</b>
  <span>Leading on the 50 DMA: ${top}</span><span>Lagging: ${bot}</span>
  <span>Spread top to bottom: ${(gv(rank[0].r,'pct_above_50dma')-gv(rank[rank.length-1].r,'pct_above_50dma')).toFixed(0)} pts</span></div>
 <div class="grid2">
  <div class="card"><h3>% above 50 DMA, by sector</h3>${rows('pct_above_50dma')}
   <div class="cap">Intermediate trend health. Sectors above 60 are participating; below 30 are being sold.</div></div>
  <div class="card"><h3>% above 200 DMA, by sector</h3>${rows('pct_above_200dma')}
   <div class="cap">Long-term structure. A sector strong here but weak on the 50 DMA is correcting inside an uptrend, which is where pullback entries live.</div></div></div>
 <div class="note">Rotation read: compare the two panels. A sector rising in the left panel while its 200 DMA reading holds is
  the cleanest re-acceleration signal. A sector leading the left panel from a weak 200 DMA base is a bounce, not a trend.</div>`}

/* ---- compare ---- */
function comparePane(){const M=[['pct_above_50dma','% above 50 DMA'],['pct_above_200dma','% above 200 DMA'],
  ['pct_above_10dma','% above 10 DMA'],['pct_50dma_gt_200dma','50 DMA above 200 DMA']];
 const get=(u,k)=>DATA[u]?gv(DATA[u].rows[0],k):null;
 const a=get('ALL','pct_above_50dma'),f=get('FNO','pct_above_50dma');
 const sm=get('SMALLCAP250','pct_above_50dma'),n5=get('NIFTY50','pct_above_50dma');
 const o=[];
 if(a!=null&&f!=null)o.push(`<span>Liquid F&amp;O basket vs full market on 50 DMA: ${f.toFixed(0)}% vs ${a.toFixed(0)}%, gap ${(f-a).toFixed(0)} pts</span>`);
 if(sm!=null&&n5!=null)o.push(`<span>Smallcap 250 vs Nifty 50: ${sm.toFixed(0)}% vs ${n5.toFixed(0)}%, ${sm>n5?'risk appetite broadening down the cap curve':'large caps holding better, defensive tilt'}</span>`);
 document.getElementById('p-compare').innerHTML=`<div class="obs"><b>Key observations</b>${o.join('')}</div>`+
 `<div class="grid2">`+M.map(([k,l])=>`<div class="card"><h3>${l}</h3>`+
  SIZES.map(u=>{const v=get(u,k);if(v==null)return'';
   return`<div class="cr"><span class="cn">${ULBL[u]}</span><div class="ct">
   <div class="cf" style="width:${v}%;background:${bc(v)}"></div></div><span class="cv">${v.toFixed(0)}%</span></div>`}).join('')
  +`</div>`).join('')+`</div>`}

/* ---- regime timeline ---- */
function regimePane(){const RC={'broad uptrend':'#2c8f57','narrowing uptrend':'#5b8f3f','corrective, mixed':'#8a7a33',
  'distribution':'#9c5730','washed out':'#8f3a2c','insufficient data':'#3a444d'};
 const tot=RUNS.reduce((s,r)=>s+r.n,0);
 const bar=RUNS.map(r=>`<div style="flex:${r.n};background:${RC[r.r]}" title="${r.r} · ${r.from} to ${r.to} · ${r.n} sessions"></div>`).join('');
 const cur=RUNS[RUNS.length-1];
 const longest=RUNS.slice().sort((a,b)=>b.n-a.n)[0];
 const tbl=RUNS.slice().reverse().slice(0,14).map(r=>
  `<tr><td style="color:${RC[r.r]};font-weight:700">${r.r}</td><td>${r.from} &rarr; ${r.to}</td><td class="n">${r.n} sess</td></tr>`).join('');
 document.getElementById('p-regime').innerHTML=`
 <div class="obs"><b>Key observations</b>
  <span>Current regime: ${cur.r}, ${cur.n} sessions and counting</span>
  <span>Longest run on record: ${longest.r}, ${longest.n} sessions</span>
  <span>${RUNS.length} regime changes across ${tot} sessions</span></div>
 <div class="card"><h3>Regime timeline &mdash; All NSE, oldest left</h3><div class="rt">${bar}</div>
  <div class="rl">${Object.entries(RC).filter(([k])=>k!=='insufficient data').map(([k,v])=>`<span><i style="background:${v}"></i>${k}</span>`).join('')}</div>
  <div class="cap">Each block is a continuous run at one regime. Width is proportional to how long it lasted. Hover for dates.
   Short alternating blocks mean a chopping market where breadth signals are unreliable; long blocks mean a trend worth positioning behind.</div></div>
 <div class="card"><h3>Recent regime runs</h3><table class="runs">${tbl}</table></div>`}

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
function guidePane(){document.getElementById('p-guide').innerHTML=`
 <div class="note"><b>The single most important idea.</b> Pradeep Bonde, whose Market Monitor this dashboard descends from,
  is explicit that breadth is most useful at extremes and is close to noise between them. The 5-day ratio reaching an extreme is
  the actionable event, not the day-to-day wiggle. He also notes an asymmetry worth remembering: extremely bearish breadth is a
  reliable bullish signal, while extremely bullish breadth has a poor record of calling tops, because tops are gradual and bottoms
  are violent. Use breadth to add risk after washouts and to trim risk gradually, not to time exits precisely.</div>
 <div class="gd">
 <div class="gs"><h4>India calibration, why the US numbers do not transfer</h4>
  <dl><dd>Bonde's thresholds of 2.0 bullish and 0.5 bearish on the 5-day ratio were set on a US universe of roughly 7,200 common
   stocks. The Indian EQ universe carries around 2,400 names, thousands of which are thin microcaps that clear 4% on trivial volume.
   That gives the Indian ratio a structural upward bias: its median sits near 1.7, so a reading of 2.0 is an ordinary day, not a
   signal. Every threshold below is calibrated on this dashboard's own history.</dd></dl>
  <table>
   <tr><td>5-day ratio, aggressive extreme</td><td>above 5.0</td></tr>
   <tr><td>5-day ratio, neutral band</td><td>0.9 to 3.1</td></tr>
   <tr><td>5-day ratio, defensive extreme</td><td>below 0.5</td></tr>
   <tr><td>10-day ratio, aggressive</td><td>above 3.5</td></tr>
   <tr><td>10-day ratio, defensive</td><td>below 0.7</td></tr>
  </table></div>
 <div class="gs"><h4>Regime bands, % above 50 DMA</h4>
  <table>
   <tr><td>Broad uptrend</td><td>above 60</td></tr>
   <tr><td>Narrowing uptrend</td><td>45 to 60</td></tr>
   <tr><td>Corrective, mixed</td><td>25 to 45</td></tr>
   <tr><td>Distribution</td><td>12 to 25</td></tr>
   <tr><td>Washed out</td><td>below 12</td></tr>
  </table>
  <dl><dt>How to use</dt><dd>Regime sets position size, not direction. Broad uptrend supports full exposure and breakout entries.
   Narrowing means keep positions but stop adding. Corrective means selective, smaller size. Distribution means defensive.
   Washed out is where the best risk-reward entries appear, and it is also the hardest place to buy.</dd></dl></div>
 <div class="gs"><h4>Flags</h4>
  <dl><dt>THRUST</dt><dd>At least 10% of the universe up 4% in one session, with at least three times as many 4% risers as fallers.
   After a decline this is the classic signature of a durable low. Inside an established uptrend it means less.</dd>
  <dt>BEAR DIV</dt><dd>The Nifty made a 20-session high but % above 50 DMA did not. Fewer stocks are carrying the index.
   Not a sell signal on its own; it is a warning that raises the value of tight stops.</dd>
  <dt>BULL DIV</dt><dd>The Nifty made a 20-session low but % above 50 DMA did not. Selling is concentrated in the index heavyweights
   while the broad market is already repairing. Historically the more reliable of the two.</dd></dl></div>
 <div class="gs"><h4>Column glossary</h4>
  <dl><dt>% above 10 / 20 / 50 / 200 DMA</dt><dd>Share of stocks trading above that simple moving average. 50 is the primary regime gauge.</dd>
  <dt>Ext</dt><dd>Share of stocks more than 15% above their 50 DMA. A froth gauge, not a strength gauge. Readings above 30 mean the
   move is stretched and pullback risk is elevated; very low readings during an advance mean the move still has room.</dd>
  <dt>10&gt;20, 20&gt;50, 50&gt;200</dt><dd>Share of stocks whose shorter moving average sits above the longer. Slower and cleaner than
   raw percent-above, because it ignores single-day noise.</dd>
  <dt>52wH, 52wL, H/L</dt><dd>New 52-week highs and lows, and the ratio highs / (highs + lows). Above 0.85 is broad strength,
   below 0.20 is broad weakness. The ratio is more useful than either raw count.</dd>
  <dt>U4, D4, Net4</dt><dd>Stocks up or down 4% or more, close to close, and the difference. The daily pulse.</dd>
  <dt>U10, D10</dt><dd>The same at a 10% threshold. Far rarer, so a cluster marks genuine dislocation rather than ordinary volatility.</dd>
  <dt>R5, R10</dt><dd>Stockbee's primary ratios: five or ten sessions of 4% risers divided by the same window of 4% fallers.</dd>
  <dt>U25m, D25m</dt><dd>Stocks moving 25% or more over 21 sessions, roughly a month.</dd>
  <dt>U25q, D25q</dt><dd>The same over 63 sessions, a quarter. Bonde rates the quarterly version his single most important
   indicator; a collapsing U25q count marks a deteriorating environment even while the index looks fine.</dd>
  <dt>U20w, D20w</dt><dd>Stocks moving 20% or more in five sessions. The momentum-burst signal.</dd></dl></div>
 <div class="gs"><h4>Regime timeline, how to read it</h4>
  <dl><dd>Each coloured block is a continuous run at one regime, with width proportional to its length. The value is in the pattern,
   not any single block. Long uninterrupted blocks mean a trending market where breadth signals carry information and positions
   can be held. Rapid alternation between narrow and corrective means a chopping market where every breadth signal will whipsaw you;
   in that state the correct response is smaller size and shorter holding periods, not more analysis. Watch also for how quickly the
   market moves from broad uptrend to corrective: fast transitions tend to resolve back upward, slow grinding ones tend not to.</dd></dl></div>
 <div class="gs"><h4>Colour logic</h4>
  <dl><dd>Fixed absolute thresholds, never percentiles. A percentile scale repaints itself as conditions change, which hides the
   regime shifts this dashboard exists to reveal. Percentage columns are graded on 0 to 100 with tighter transitions around the
   42 to 58 band where the regime call actually flips. Count columns are converted to a share of that day's universe first, so a
   number means the same thing whether the universe holds 2,400 names or 2,600. Down-columns are inverted, so red always reads
   bearish regardless of which column it sits in. Long stretches of mid-tone colour are the truth, not a fault in the scale.</dd></dl></div>
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
function draw(){const d=DATA[U];if(!d)return;
 if(TAB==='table'){pills(d);bars(d);obsP(d);body(d)}
 else if(TAB==='charts')chartPane();else if(TAB==='sectors')sectorPane();
 else if(TAB==='compare')comparePane();else if(TAB==='regime')regimePane();
 else if(TAB==='scanner')scannerPane();else if(TAB==='guide')guidePane()}
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
