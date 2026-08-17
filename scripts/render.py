#!/usr/bin/env python3
"""
Render breadth_history.csv into a self-contained HTML dashboard.

Colour: continuous gradient with asymmetric anchoring at regime decision points.
Layout: infographic bars + dense table per universe, compare tab for all segments.
"""

import argparse, glob, json, os
import numpy as np, pandas as pd

GROUPS = [
    ("Breadth", [("advances", "Adv", None), ("declines", "Dec", None)]),
    ("Daily momentum", [("up_4pct", "Up 4%", "up4"), ("down_4pct", "Dn 4%", "dn4"),
                        ("net_4pct", "Net 4%", None)]),
    ("Range movers", [("up_25pct_21d", "Up 25% 1M", "up25"), ("down_25pct_21d", "Dn 25% 1M", "dn25"),
                      ("up_20pct_5d", "Up 20% 5D", None), ("down_20pct_5d", "Dn 20% 5D", None)]),
    ("Trend, % above DMA", [("pct_above_10dma", "10", None), ("pct_above_20dma", "20", None),
                            ("pct_above_50dma", "50", None), ("pct_above_200dma", "200", None)]),
    ("MA structure", [("pct_10dma_gt_20dma", "10>20", None), ("pct_20dma_gt_50dma", "20>50", None),
                      ("pct_50dma_gt_200dma", "50>200", None)]),
    ("Extremes", [("new_52w_high", "52w H", "hi52"), ("new_52w_low", "52w L", "lo52")]),
    ("Nifty", [("nifty_close", "Close", None), ("nifty_chg_pct", "Chg %", None)]),
]
COLS = [(k, l, lk) for _, cs in GROUPS for k, l, lk in cs]
PCT_FMT = {k for k, _, _ in COLS if k.startswith("pct_")}

DMA_ANCHORS = [(0, 0.0), (15, 0.12), (30, 0.28), (42, 0.42), (50, 0.50),
               (58, 0.58), (70, 0.72), (85, 0.88), (100, 1.0)]

COUNT_PROFILES = {
    "ALL": {
        "advances": [(20, 0.05), (35, 0.2), (45, 0.4), (50, 0.5), (55, 0.6), (65, 0.8), (80, 0.95)],
        "up_4pct":  [(0.5, 0.1), (2, 0.25), (3.5, 0.4), (5, 0.55), (7, 0.7), (10, 0.85), (15, 0.95)],
        "net_4pct": [(-10, 0.05), (-5, 0.15), (-2, 0.35), (0, 0.5), (2, 0.65), (5, 0.85), (10, 0.95)],
        "up_25pct_21d": [(0.2, 0.1), (0.6, 0.2), (1.5, 0.4), (3, 0.55), (5, 0.7), (8, 0.85), (12, 0.95)],
        "up_20pct_5d": [(0.05, 0.1), (0.15, 0.2), (0.3, 0.4), (0.6, 0.55), (1, 0.7), (1.8, 0.85), (3, 0.95)],
        "new_52w_high": [(0.2, 0.1), (0.8, 0.2), (1.5, 0.35), (3, 0.5), (5, 0.65), (8, 0.8), (12, 0.95)],
    },
    "FNO": {
        "advances": [(20, 0.05), (35, 0.2), (45, 0.4), (50, 0.5), (55, 0.6), (65, 0.8), (80, 0.95)],
        "up_4pct":  [(0.3, 0.15), (0.8, 0.3), (1.5, 0.45), (2.5, 0.55), (4, 0.7), (6, 0.85), (10, 0.95)],
        "net_4pct": [(-7, 0.05), (-3.5, 0.15), (-1, 0.35), (0, 0.5), (1, 0.65), (3.5, 0.85), (7, 0.95)],
        "up_25pct_21d": [(0.1, 0.1), (0.3, 0.2), (0.6, 0.4), (1.2, 0.55), (2, 0.7), (4, 0.85), (7, 0.95)],
        "up_20pct_5d": [(0.05, 0.15), (0.1, 0.3), (0.2, 0.45), (0.4, 0.55), (0.7, 0.7), (1, 0.85), (2, 0.95)],
        "new_52w_high": [(0.3, 0.1), (1, 0.2), (2, 0.35), (3.5, 0.5), (5.5, 0.65), (8, 0.8), (13, 0.95)],
    },
}
# small-universe profiles (Nifty 50, Next 50)
COUNT_PROFILES["NIFTY50"] = COUNT_PROFILES["FNO"].copy()
COUNT_PROFILES["NIFTYNEXT50"] = COUNT_PROFILES["FNO"].copy()
COUNT_PROFILES["MIDCAP150"] = COUNT_PROFILES["FNO"].copy()
COUNT_PROFILES["SMALLCAP250"] = COUNT_PROFILES["ALL"].copy()
COUNT_PROFILES["NIFTY500"] = COUNT_PROFILES["ALL"].copy()

# mirror profiles for inverted columns
for u in COUNT_PROFILES:
    p = COUNT_PROFILES[u]
    if "advances" in p:
        p["declines"] = p["advances"]
    if "up_4pct" in p:
        p["down_4pct"] = p["up_4pct"]
    if "up_25pct_21d" in p:
        p["down_25pct_21d"] = p["up_25pct_21d"]
    if "up_20pct_5d" in p:
        p["down_20pct_5d"] = p["up_20pct_5d"]
    if "new_52w_high" in p:
        p["new_52w_low"] = p["new_52w_high"]

INVERTED = {"declines", "down_4pct", "down_25pct_21d", "down_20pct_5d", "new_52w_low"}
UNIVERSE_LABELS = {
    "ALL": "All NSE", "FNO": "F&O", "NIFTY50": "Nifty 50", "NIFTYNEXT50": "Next 50",
    "MIDCAP150": "Midcap 150", "SMALLCAP250": "Smallcap 250", "NIFTY500": "Nifty 500",
}
UNIVERSE_ORDER = ["ALL", "FNO", "NIFTY50", "NIFTYNEXT50", "MIDCAP150", "SMALLCAP250", "NIFTY500"]


def shade(universe, key, value, n):
    if value is None or (isinstance(value, float) and np.isnan(value)):
        return None
    # percentage columns
    if key.startswith("pct_"):
        xp = [a[0] for a in DMA_ANCHORS]
        fp = [a[1] for a in DMA_ANCHORS]
        return round(float(np.clip(np.interp(value, xp, fp), 0, 1)), 3)
    # nifty change
    if key == "nifty_chg_pct":
        xp = [-3, -1.5, -0.5, 0, 0.5, 1.5, 3]
        fp = [0.0, 0.1, 0.35, 0.5, 0.65, 0.9, 1.0]
        return round(float(np.clip(np.interp(value, xp, fp), 0, 1)), 3)
    # nifty close: no shade
    if key == "nifty_close":
        return None
    # count columns
    prof = COUNT_PROFILES.get(universe, COUNT_PROFILES.get("ALL", {}))
    base = key
    if base == "net_4pct":
        anchors = prof.get("net_4pct")
        if not anchors:
            return None
        xp = [a[0] for a in anchors]
        fp = [a[1] for a in anchors]
        v = value / n * 100
        return round(float(np.clip(np.interp(v, xp, fp), 0, 1)), 3)
    anchors = prof.get(base)
    if not anchors:
        return None
    xp = [a[0] for a in anchors]
    fp = [a[1] for a in anchors]
    v = value / n * 100
    score = float(np.clip(np.interp(v, xp, fp), 0, 1))
    if key in INVERTED:
        score = 1.0 - score
    return round(score, 3)


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
        return "distribution / downtrend"
    return "washed out"


def observations(g):
    """3-5 auto-generated observations for a universe."""
    obs = []
    if len(g) < 10:
        return obs
    a50 = g["pct_above_50dma"].values
    a10 = g["pct_above_10dma"].values
    a200 = g["pct_above_200dma"].values
    n4 = g["net_4pct"].values

    # 5-session trend
    d5 = a50[-5:]
    if len(d5) == 5:
        if all(d5[i] >= d5[i - 1] for i in range(1, 5)):
            obs.append(f"% above 50 DMA rising for 5 sessions ({d5[0]:.1f} to {d5[-1]:.1f})")
        elif all(d5[i] <= d5[i - 1] for i in range(1, 5)):
            obs.append(f"% above 50 DMA falling for 5 sessions ({d5[0]:.1f} to {d5[-1]:.1f})")

    # 10 vs 50 DMA divergence
    if len(a10) >= 2 and len(a50) >= 2:
        gap = a10[-1] - a50[-1]
        if gap > 15:
            obs.append(f"Short-term leads intermediate by {gap:.0f} pts (momentum running hot)")
        elif gap < -10:
            obs.append(f"Short-term trails intermediate by {abs(gap):.0f} pts (momentum lagging)")

    # 50 vs 200 DMA regime alignment
    if not np.isnan(a50[-1]) and not np.isnan(a200[-1]):
        if a50[-1] > 55 and a200[-1] > 55:
            obs.append("Short, medium and long-term trends aligned bullish")
        elif a50[-1] < 40 and a200[-1] < 45:
            obs.append("Short, medium and long-term trends aligned bearish")
        elif a50[-1] < 45 and a200[-1] > 55:
            obs.append("Intermediate trend weakening while long-term holds (corrective phase)")

    # recent flags
    flags = []
    for _, r in g.tail(5).iterrows():
        d = r["date"].strftime("%d/%m")
        if r.get("thrust", False):
            flags.append(f"thrust on {d}")
        if r.get("div_bearish", False):
            flags.append(f"bearish divergence on {d}")
        if r.get("div_bullish", False):
            flags.append(f"bullish divergence on {d}")
    if flags:
        obs.append("Recent signals: " + ", ".join(flags))

    # 52w high/low ratio
    h, l = int(g.iloc[-1].get("new_52w_high", 0)), int(g.iloc[-1].get("new_52w_low", 0))
    if h + l > 5:
        ratio = h / max(l, 1)
        if ratio > 5:
            obs.append(f"New highs dominating lows {h}:{l} (broad strength)")
        elif ratio < 0.3:
            obs.append(f"New lows dominating highs {l}:{h} (broad weakness)")
    return obs[:5]


def load_lists(csv_dir, keep=30):
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
    lists = load_lists(os.path.dirname(os.path.abspath(csv)))

    # handle column name variations
    if "pct_20dma_gt_40dma" in df.columns and "pct_20dma_gt_50dma" not in df.columns:
        df["pct_20dma_gt_50dma"] = df["pct_20dma_gt_40dma"]

    payload, summary = {}, {}
    avail = [u for u in UNIVERSE_ORDER if u in df["universe"].unique()]
    for u in avail:
        g = df[df.universe == u].sort_values("date").reset_index(drop=True)
        gg = g.iloc[-rows:] if rows else g
        recs = []
        for _, r in gg.iterrows():
            n = int(r["universe_count"])
            rec = {"d": r["date"].strftime("%d/%m/%y"), "iso": r["date"].strftime("%Y-%m-%d"),
                   "wd": r["date"].strftime("%a"), "n": n,
                   "f": [x for x, k in [("bear", "div_bearish"), ("bull", "div_bullish"),
                                        ("thrust", "thrust")] if bool(r.get(k, False))],
                   "v": {}, "c": {}}
            for k, _, _ in COLS:
                val = r.get(k, np.nan)
                val = None if pd.isna(val) else (round(float(val), 2) if k in PCT_FMT or
                                                 k.startswith("nifty") else int(val))
                rec["v"][k] = val
                s = shade(u, k, val, n)
                if s is not None:
                    rec["c"][k] = s
            recs.append(rec)
        recs.reverse()
        last = gg.iloc[-1]
        obs = observations(gg)
        payload[u] = {"rows": recs, "regime": regime(last),
                      "asof": last["date"].strftime("%d %b %Y"), "sessions": int(len(gg)),
                      "obs": obs, "label": UNIVERSE_LABELS.get(u, u)}
        summary[u] = {"asof": last["date"].strftime("%Y-%m-%d"), "n": int(last["universe_count"]),
                      "regime": regime(last), "a10": last.get("pct_above_10dma"),
                      "a50": last.get("pct_above_50dma"), "a200": last.get("pct_above_200dma"),
                      "net4": int(last["net_4pct"]), "flags": recs[0]["f"]}

    html = (TEMPLATE
            .replace("__DATA__", json.dumps(payload, separators=(",", ":")))
            .replace("__GROUPS__", json.dumps([[gl, [[k, l, lk] for k, l, lk in cs]] for gl, cs in GROUPS]))
            .replace("__LISTS__", json.dumps(lists, separators=(",", ":")))
            .replace("__AVAIL__", json.dumps(avail))
            .replace("__REPO__", repo or ""))
    with open(out, "w") as f:
        f.write(html)

    v = os.path.join(os.path.dirname(csv), "validation.txt")
    print(f"dashboard -> {out}  ({os.path.getsize(out) // 1024} KB)")
    for u, s in summary.items():
        print(f"[{u}] {s['asof']} n={s['n']} | >10DMA {s['a10']}% >50DMA {s['a50']}% "
              f">200DMA {s['a200']}% | net4 {s['net4']:+d} | {s['regime']}"
              + (f" | flags: {', '.join(s['flags'])}" if s["flags"] else ""))
    print("validation:", open(v).read().strip().replace("\n", " / ") if os.path.exists(v) else "not found")


TEMPLATE = r"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Market Breadth</title>
<style>
:root{--paper:#e9ece6;--panel:#f5f7f3;--ink:#16201c;--muted:#5d6b63;--rule:#c8d0c6;--acc:#3f8a4f}
*{box-sizing:border-box}
body{margin:0;background:var(--paper);color:var(--ink);font:13px/1.4 ui-sans-serif,-apple-system,"Segoe UI",Roboto,sans-serif}
.wrap{max-width:1720px;margin:0 auto;padding:10px 14px 40px}
.top{display:flex;flex-wrap:wrap;gap:8px 14px;align-items:baseline;justify-content:space-between;
 border-bottom:2px solid var(--ink);padding-bottom:6px}
h1{font-size:15px;margin:0;display:inline;letter-spacing:-.01em}
.as{color:var(--muted);font-size:10px;letter-spacing:.06em;text-transform:uppercase;margin-left:8px}
.ctl{display:flex;gap:3px;align-items:center;flex-wrap:wrap}
.tab{padding:3px 10px;border:1px solid var(--rule);background:var(--panel);cursor:pointer;font-size:10.5px;
 font-weight:600;border-radius:2px;white-space:nowrap}
.tab[aria-selected=true]{background:var(--ink);color:var(--paper);border-color:var(--ink)}
.tab.cmp{border-color:var(--acc);color:var(--acc)}
.tab.cmp[aria-selected=true]{background:var(--acc);color:#fff;border-color:var(--acc)}
select{border:1px solid var(--rule);background:var(--panel);padding:2px 5px;border-radius:2px;font:inherit;font-size:10.5px}
/* pills */
.pills{display:flex;flex-wrap:wrap;gap:0;border:1px solid var(--rule);border-top:0;background:var(--panel);margin-bottom:0}
.pill{padding:4px 11px;border-right:1px solid var(--rule);min-width:80px}
.pill:last-child{border-right:0}
.pill .k{font-size:8px;letter-spacing:.1em;text-transform:uppercase;color:var(--muted)}
.pill .v{font:14px/1.2 ui-monospace,"SF Mono",Menlo,monospace;font-variant-numeric:tabular-nums;margin-top:1px}
.pill.reg{min-width:180px}.pill.reg .v{font-family:inherit;font-size:12px;font-weight:700}
/* bars */
.bars{display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:0;
 border:1px solid var(--rule);border-top:0;background:var(--panel);padding:8px 12px;margin-bottom:8px}
.bar-g{margin-bottom:5px}.bar-g:last-child{margin-bottom:0}
.bar-l{font-size:9px;letter-spacing:.09em;text-transform:uppercase;color:var(--muted);margin-bottom:2px}
.bar-t{height:18px;background:#dde1d8;border-radius:1px;position:relative;overflow:hidden}
.bar-f{height:100%;border-radius:1px;transition:width .3s}
.bar-v{position:absolute;right:5px;top:1px;font:11px/16px ui-monospace,Menlo,monospace;color:var(--ink)}
/* observations */
.obs{border:1px solid var(--rule);border-top:0;background:var(--panel);padding:5px 12px 6px;margin-bottom:8px;
 font-size:11.5px;color:var(--ink);line-height:1.55}
.obs span{display:inline-block;margin-right:14px}
.obs span::before{content:"\25cf ";font-size:7px;color:var(--muted);vertical-align:1px}
/* table */
.tblwrap{overflow:auto;max-height:74vh;border:1px solid var(--rule);background:var(--panel)}
table{border-collapse:separate;border-spacing:0;width:100%;
 font:11px ui-monospace,"SF Mono",Menlo,monospace;font-variant-numeric:tabular-nums}
thead th{position:sticky;background:var(--ink);color:var(--paper);z-index:2}
tr.grp th{top:0;font-size:8px;letter-spacing:.13em;text-transform:uppercase;font-weight:600;
 padding:2px 4px;color:#9fb3a6;border-bottom:1px solid #2c3a33}
tr.col th{top:18px;font-size:9.5px;font-weight:600;padding:3px 4px;text-align:right;white-space:nowrap}
th.gs,td.gs{border-left:2px solid var(--rule)}
tr.grp th.gs{border-left:2px solid #55665c}tr.col th.gs{border-left:2px solid #55665c}
td{padding:2px 4px;text-align:right;white-space:nowrap;border-bottom:1px solid #e5e9e1}
td.d,th.d{text-align:left;position:sticky;left:0;background:var(--panel);z-index:1;
 border-right:2px solid var(--rule);font-weight:600;padding-left:6px}
th.d{background:var(--ink);z-index:4}
td.fg{text-align:left;padding:0 3px}
tr:hover td{background:#e0e6da!important}
.fl{display:inline-block;font-size:7.5px;padding:1px 3px;border-radius:2px;margin-right:1px;
 letter-spacing:.04em;text-transform:uppercase;font-family:ui-sans-serif,sans-serif}
.fl.bear{background:#b3402f;color:#fff}.fl.bull{background:#3f8a4f;color:#fff}
.fl.thrust{background:var(--ink);color:var(--paper)}
td.lk{cursor:pointer}td.lk:hover{outline:1.5px solid var(--ink);outline-offset:-1.5px}
/* compare */
.cmp-wrap{display:none;padding:12px 0}
.cmp-wrap.on{display:block}
.cmp-section{margin-bottom:18px}
.cmp-section h3{font-size:13px;margin:0 0 6px;font-weight:700}
.cmp-row{display:flex;align-items:center;gap:8px;margin-bottom:3px}
.cmp-name{width:90px;font-size:11px;font-weight:600;text-align:right;flex-shrink:0}
.cmp-track{flex:1;height:16px;background:#dde1d8;border-radius:1px;position:relative;overflow:hidden;max-width:400px}
.cmp-fill{height:100%;border-radius:1px}
.cmp-val{font:11px ui-monospace,Menlo,monospace;width:45px;text-align:right;flex-shrink:0}
/* defs */
details.defs{margin-top:10px;border:1px solid var(--rule);background:var(--panel)}
details.defs summary{padding:6px 10px;font-size:11px;font-weight:600;cursor:pointer;
 letter-spacing:.04em;text-transform:uppercase;color:var(--muted)}
.def-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(280px,1fr));gap:6px 14px;
 padding:6px 12px 12px;font-size:11px;line-height:1.5;color:var(--ink)}
.def-grid b{color:var(--ink)}
/* overlay */
#ov{position:fixed;inset:0;background:rgba(20,28,24,.45);display:none;align-items:center;justify-content:center;z-index:9}
#ov.on{display:flex}
#bx{background:var(--panel);border:1px solid var(--ink);max-width:720px;max-height:76vh;overflow:auto;padding:14px 16px}
#bx h3{margin:0 0 2px;font-size:13px}#bx .sub{color:var(--muted);font-size:10.5px;margin-bottom:8px}
#bx .syms{font:11px ui-monospace,Menlo,monospace;columns:4;column-gap:16px;line-height:1.7}
#bx button{margin-top:10px;border:1px solid var(--ink);background:var(--ink);color:var(--paper);
 padding:3px 10px;cursor:pointer;font:inherit;font-size:10.5px}
.legend{display:flex;gap:10px;align-items:center;margin-top:7px;color:var(--muted);font-size:10px;flex-wrap:wrap}
.grad{width:140px;height:10px;border-radius:1px}
footer{margin-top:7px;color:var(--muted);font-size:10px;line-height:1.5}
@media(max-width:760px){.pill{min-width:60px}.pill.reg{min-width:100%}.cmp-track{max-width:200px}}
</style></head><body><div class="wrap">
<div class="top"><div><h1>Market Breadth</h1><span class="as" id="as"></span></div>
<div class="ctl" id="tabs"></div></div>
<div id="main">
<div class="pills" id="pills"></div>
<div class="bars" id="bars"></div>
<div class="obs" id="obs"></div>
<div class="tblwrap"><table id="t"><thead></thead><tbody></tbody></table></div>
</div>
<div class="cmp-wrap" id="cmp"></div>
<div class="legend"><span>Continuous gradient, fixed thresholds</span>
<canvas class="grad" id="gc" width="140" height="10"></canvas>
<span>bearish &rarr; bullish. Counts graded as share of universe. Inverted columns: red = bearish</span></div>
<details class="defs"><summary>Reference: flags, columns, regime definitions</summary>
<div class="def-grid">
<div><b>THRUST</b> 10%+ of the universe up 4% in a session, with at least 3x as many 4% risers as 4% fallers. Historically marks durable lows after a decline.</div>
<div><b>BEAR DIV</b> Nifty makes a 20-session high but % above 50 DMA does not. Internal deterioration under a rising index. Often precedes a correction.</div>
<div><b>BULL DIV</b> Nifty makes a 20-session low but % above 50 DMA does not. Internal improvement under a falling index. Marks accumulation.</div>
<div><b>% above DMA</b> Share of stocks whose adjusted close sits above their simple moving average. 50 DMA is the primary regime gauge.</div>
<div><b>MA structure (10&gt;20, 20&gt;50, 50&gt;200)</b> Share of stocks whose shorter-term MA sits above the longer. Measures trend stacking, slower and cleaner than raw percent-above.</div>
<div><b>Net 4%</b> Stocks up 4%+ minus stocks down 4%+, close to close. Positive = more strong movers on the buy side.</div>
<div><b>Up/Dn 25% 1M</b> Stocks that moved 25%+ in 21 trading sessions. Measures range expansion beyond daily noise.</div>
<div><b>52w H / 52w L</b> Adjusted close at or within 0.1% of the 250-session extreme. Click the cell to see the stock names.</div>
<div><b>Regime: broad uptrend</b> >60% above 50 DMA. Most stocks in intermediate uptrends.</div>
<div><b>Regime: narrowing</b> 45-60% above 50 DMA. Participation fading. Watch for bear divergences.</div>
<div><b>Regime: corrective</b> 25-45% above 50 DMA. Mixed. Selective, not directional.</div>
<div><b>Regime: distribution</b> 12-25% above 50 DMA. Broad selling. Risk-off.</div>
<div><b>Regime: washed out</b> &lt;12% above 50 DMA. Oversold extreme. Prior thrust signals become actionable.</div>
<div><b>Colour logic</b> Fixed absolute thresholds, not percentiles. % columns graded on 0-100 with tighter bands around the 42-58% regime boundary. Count columns converted to % of universe first. Inverted so red always means bearish.</div>
</div></details>
<footer id="ft"></footer>
</div>
<div id="ov"><div id="bx"><h3 id="bh"></h3><div class="sub" id="bs"></div><div class="syms" id="by"></div>
<button onclick="document.getElementById('ov').classList.remove('on')">Close</button></div></div>
<script>
const DATA=__DATA__, GROUPS=__GROUPS__, LISTS=__LISTS__, AVAIL=__AVAIL__, REPO="__REPO__";
const ULBL={"ALL":"All NSE","FNO":"F&O","NIFTY50":"Nifty 50","NIFTYNEXT50":"Next 50",
 "MIDCAP150":"Midcap 150","SMALLCAP250":"Smallcap 250","NIFTY500":"Nifty 500"};
const LBL={up4:"up 4%+",dn4:"down 4%+",hi52:"52-week high",lo52:"52-week low",up25:"up 25%+ in 21 sessions",dn25:"down 25%+ in 21 sessions"};
let U=AVAIL[0]||"ALL", N=250;

/* gradient */
const STOPS=[[0,.70,.25,.18],[.20,.82,.48,.31],[.35,.91,.73,.47],[.47,.95,.93,.86],
 [.53,.87,.91,.75],[.65,.76,.84,.54],[.80,.53,.71,.39],[1,.25,.54,.31]];
function clr(s){if(s===null||s===undefined)return'transparent';
 let a=STOPS[0],b=STOPS[STOPS.length-1];
 for(let i=0;i<STOPS.length-1;i++){if(s>=STOPS[i][0]&&s<=STOPS[i+1][0]){a=STOPS[i];b=STOPS[i+1];break;}}
 const t=(s-a[0])/(b[0]-a[0]||1);
 return`rgb(${(a[1]+t*(b[1]-a[1]))*255|0},${(a[2]+t*(b[2]-a[2]))*255|0},${(a[3]+t*(b[3]-a[3]))*255|0})`;}

function barClr(v){return clr(Math.max(0,Math.min(1,v/100)));}
const fmt=(k,v)=>v==null?'':k==='nifty_close'?v.toLocaleString('en-IN',{maximumFractionDigits:0})
 :(k.startsWith('pct_')?v.toFixed(1):k==='nifty_chg_pct'?v.toFixed(2):v);

/* tabs */
function buildTabs(){const el=document.getElementById('tabs');let h='';
 AVAIL.forEach(u=>h+=`<div class="tab" data-u="${u}" aria-selected="${u===U}">${ULBL[u]||u}</div>`);
 h+=`<div class="tab cmp" data-u="CMP" aria-selected="false">Compare</div>`;
 h+=`<select id="rows"><option value="60">60</option><option value="120">120</option><option value="250" selected>250</option><option value="0">All</option></select>`;
 el.innerHTML=h;
 el.querySelectorAll('.tab').forEach(t=>t.onclick=()=>{
  el.querySelectorAll('.tab').forEach(x=>x.setAttribute('aria-selected',x===t));
  const u=t.dataset.u;if(u==='CMP'){showCompare();}else{U=u;hideCompare();draw();}});
 document.getElementById('rows').onchange=e=>{N=+e.target.value;draw();};}

function showCompare(){document.getElementById('main').style.display='none';
 const c=document.getElementById('cmp');c.classList.add('on');renderCompare();}
function hideCompare(){document.getElementById('main').style.display='';
 document.getElementById('cmp').classList.remove('on');}

/* pills + bars + obs */
function pills(d){const r=d.rows[0],p=[];
 p.push(`<div class="pill reg"><div class="k">Regime &middot; ${d.label}</div><div class="v">${d.regime}</div></div>`);
 [['pct_above_10dma','>10 DMA'],['pct_above_50dma','>50 DMA'],['pct_above_200dma','>200 DMA']]
  .forEach(([k,l])=>p.push(`<div class="pill"><div class="k">${l}</div><div class="v">${r.v[k]==null?'--':r.v[k].toFixed(1)+'%'}</div></div>`));
 const n4=r.v.net_4pct;
 p.push(`<div class="pill"><div class="k">Net 4%</div><div class="v" style="color:${n4>0?'var(--acc)':'#b3402f'}">${n4>0?'+':''}${n4}</div></div>`);
 p.push(`<div class="pill"><div class="k">Universe</div><div class="v">${r.n}</div></div>`);
 document.getElementById('pills').innerHTML=p.join('');
 document.getElementById('as').textContent=`as of ${d.asof} \u00b7 ${r.n} stocks \u00b7 ${d.sessions} sessions`;}

function bars(d){const r=d.rows[0],m=[
 ['pct_above_10dma','% above 10 DMA'],['pct_above_50dma','% above 50 DMA'],['pct_above_200dma','% above 200 DMA']];
 let h='';m.forEach(([k,l])=>{const v=r.v[k];if(v==null)return;
  h+=`<div class="bar-g"><div class="bar-l">${l}</div><div class="bar-t"><div class="bar-f" style="width:${v}%;background:${barClr(v)}"></div><div class="bar-v">${v.toFixed(1)}%</div></div></div>`;});
 document.getElementById('bars').innerHTML=h;}

function obsPanel(d){const el=document.getElementById('obs');
 if(!d.obs||!d.obs.length){el.style.display='none';return;}
 el.style.display='';el.innerHTML=d.obs.map(o=>`<span>${o}</span>`).join('');}

/* compare */
function renderCompare(){const c=document.getElementById('cmp');
 const metrics=[['pct_above_50dma','% above 50 DMA'],['pct_above_200dma','% above 200 DMA'],
  ['pct_above_10dma','% above 10 DMA'],['pct_50dma_gt_200dma','50 DMA > 200 DMA']];
 let h='';
 metrics.forEach(([k,l])=>{h+=`<div class="cmp-section"><h3>${l}</h3>`;
  AVAIL.forEach(u=>{const d=DATA[u];if(!d)return;const v=d.rows[0]&&d.rows[0].v[k];if(v==null)return;
   h+=`<div class="cmp-row"><span class="cmp-name">${ULBL[u]||u}</span><div class="cmp-track"><div class="cmp-fill" style="width:${v}%;background:${barClr(v)}"></div></div><span class="cmp-val">${v.toFixed(1)}%</span></div>`;});
  h+=`</div>`;});
 c.innerHTML=h;}

/* table */
function head(){const g=[],co=[];
 g.push('<th class="d" rowspan="2">Date</th><th rowspan="2"></th>');
 GROUPS.forEach(([gl,cs],i)=>{const s=i?' gs':'';
  g.push(`<th colspan="${cs.length}" class="${s.trim()}">${gl}</th>`);
  cs.forEach(([k,l],j)=>co.push(`<th class="${!j&&i?'gs':''}">${l}</th>`));});
 document.querySelector('#t thead').innerHTML=`<tr class="grp">${g.join('')}</tr><tr class="col">${co.join('')}</tr>`;}

function body(d){const rows=(N?d.rows.slice(0,N):d.rows).map(r=>{
 const tds=[];GROUPS.forEach(([gl,cs],i)=>cs.forEach(([k,l,lk],j)=>{
  const s=r.c[k],bg=s==null||s===undefined?'':'background:'+clr(s);
  const cl=(!j&&i?'gs ':'')+(lk&&r.v[k]>0?'lk':'');
  tds.push(`<td class="${cl.trim()}" style="${bg}"${lk&&r.v[k]>0?` data-i="${r.iso}" data-k="${lk}"`:''}>${fmt(k,r.v[k])}</td>`);}));
 return`<tr><td class="d">${r.d} ${r.wd}</td><td class="fg">${
  r.f.map(f=>`<span class="fl ${f}">${f}</span>`).join('')}</td>${tds.join('')}</tr>`;});
 document.querySelector('#t tbody').innerHTML=rows.join('');
 document.querySelectorAll('td.lk').forEach(td=>td.onclick=()=>show(td.dataset.i,td.dataset.k));}

async function show(iso,k){const bx=document.getElementById('ov');
 document.getElementById('bh').textContent=`${ULBL[U]||U} \u00b7 ${LBL[k]||k}`;
 document.getElementById('bs').textContent=iso;
 document.getElementById('by').textContent='loading...';bx.classList.add('on');
 let L=LISTS[iso];
 if(!L&&REPO){try{const r=await fetch(`https://raw.githubusercontent.com/${REPO}/main/data/lists/${iso}.json`);
  if(r.ok){L=await r.json();LISTS[iso]=L;}}catch(e){}}
 const s=L&&L[U]&&L[U][k];
 document.getElementById('by').textContent=s&&s.length?s.join('   ')
  :'Not available. Lists kept for the last 90 sessions only.';
 document.getElementById('bs').textContent=`${iso}${s?' \u00b7 '+s.length+' stocks':''}`;}

function draw(){const d=DATA[U];if(!d)return;pills(d);bars(d);obsPanel(d);body(d);}

/* gradient canvas */
function drawGrad(){const c=document.getElementById('gc'),x=c.getContext('2d');
 for(let i=0;i<140;i++){x.fillStyle=clr(i/139);x.fillRect(i,0,1,10);}}

document.getElementById('ft').innerHTML='Source: NSE UDiFF bhavcopy, series EQ. Prices chained on close/prev-close (split/bonus adjusted). F&O universe point-in-time from derivatives bhavcopy. Index segments from NSE constituent lists. 4% close-to-close; 25% over 21 sessions; 20% over 5 sessions. Divergence: 20-day Nifty extreme vs % above 50 DMA.';
document.getElementById('ov').onclick=e=>{if(e.target.id==='ov')e.currentTarget.classList.remove('on');};
buildTabs();head();draw();drawGrad();
</script></body></html>"""


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", default="data/breadth_history.csv")
    ap.add_argument("--out", default="dashboard.html")
    ap.add_argument("--rows", type=int, default=250)
    ap.add_argument("--repo", default="bobbythomas-create/market-breadth")
    a = ap.parse_args()
    build(a.csv, a.out, a.rows, a.repo)
