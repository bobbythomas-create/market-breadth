#!/usr/bin/env python3
"""
Render breadth_history.csv into a self-contained HTML dashboard.

Colour rule: absolute thresholds, not percentiles.
  - Percentage columns (% above DMA, MA stacking) are graded on fixed 0-100 bands,
    because those numbers carry intrinsic meaning.
  - Count columns are first converted to percent of that day's universe, then graded
    on fixed bands, so a count means the same thing whether the universe is 1,800 or 2,500.
  - Bands are set per universe where the distributions genuinely differ (4% movers are
    far rarer in a 200-stock F&O basket than across 2,400 names).

Usage
  python render.py --csv data/breadth_history.csv --out dashboard.html [--rows 250]
                   [--repo bobbythomas-create/market-breadth]
"""

import argparse
import glob
import json
import os
import numpy as np
import pandas as pd

# group label -> [(csv key, header label, clickable list key or None)]
GROUPS = [
    ("Breadth", [("advances", "Adv", None), ("declines", "Dec", None)]),
    ("Daily momentum", [("up_4pct", "Up 4%", "up4"), ("down_4pct", "Dn 4%", "dn4"),
                        ("net_4pct", "Net 4%", None)]),
    ("Range movers", [("up_25pct_21d", "Up 25% 1M", "up25"), ("down_25pct_21d", "Dn 25% 1M", "dn25"),
                      ("up_20pct_5d", "Up 20% 5D", None), ("down_20pct_5d", "Dn 20% 5D", None)]),
    ("Trend, % above DMA", [("pct_above_10dma", "10", None), ("pct_above_20dma", "20", None),
                            ("pct_above_50dma", "50", None), ("pct_above_200dma", "200", None)]),
    ("MA structure", [("pct_10dma_gt_20dma", "10>20", None), ("pct_20dma_gt_40dma", "20>40", None),
                      ("pct_50dma_gt_200dma", "50>200", None)]),
    ("Extremes", [("new_52w_high", "52w H", "hi52"), ("new_52w_low", "52w L", "lo52")]),
    ("Nifty", [("nifty_close", "Close", None), ("nifty_chg_pct", "Chg %", None)]),
]
COLS = [(k, l, lk) for _, cs in GROUPS for k, l, lk in cs]

DMA_BANDS = [15, 30, 42, 58, 70, 85]          # shared by every percentage column
# key -> (bands, share_of_universe?, invert?)   bands are 6 cut points making 7 colour steps
BANDS = {
    "ALL": {
        "advances":          ([25, 35, 45, 55, 65, 75], True, False),
        "declines":          ([25, 35, 45, 55, 65, 75], True, True),
        "up_4pct":           ([1, 2, 3.5, 5.5, 8, 12], True, False),
        "down_4pct":         ([1, 2, 3.5, 5.5, 8, 12], True, True),
        "net_4pct":          ([-8, -4, -1, 1, 4, 8], True, False),
        "up_25pct_21d":      ([0.3, 0.8, 1.5, 3, 5, 8], True, False),
        "down_25pct_21d":    ([0.15, 0.3, 0.5, 0.9, 1.8, 3.5], True, True),
        "up_20pct_5d":       ([0.1, 0.25, 0.5, 0.9, 1.5, 2.5], True, False),
        "down_20pct_5d":     ([0.03, 0.06, 0.12, 0.25, 0.45, 0.8], True, True),
        "new_52w_high":      ([0.3, 1, 2, 3.5, 5.5, 9], True, False),
        "new_52w_low":       ([0.3, 1, 2, 3.5, 5.5, 9], True, True),
    },
    "FNO": {
        "advances":          ([25, 35, 45, 55, 65, 75], True, False),
        "declines":          ([25, 35, 45, 55, 65, 75], True, True),
        "up_4pct":           ([0.2, 0.5, 1.2, 2.5, 4, 7], True, False),
        "down_4pct":         ([0.2, 0.5, 1.2, 2.5, 4, 7], True, True),
        "net_4pct":          ([-6, -3, -0.8, 0.8, 3, 6], True, False),
        "up_25pct_21d":      ([0.2, 0.4, 0.8, 1.5, 2.8, 5], True, False),
        "down_25pct_21d":    ([0.2, 0.45, 0.6, 1.1, 1.5, 2.5], True, True),
        "up_20pct_5d":       ([0.05, 0.15, 0.3, 0.5, 0.8, 1.2], True, False),
        "down_20pct_5d":     ([0.05, 0.15, 0.3, 0.5, 0.8, 1.2], True, True),
        "new_52w_high":      ([0.4, 1.2, 2.5, 4.5, 7, 11], True, False),
        "new_52w_low":       ([0.4, 1.2, 2.5, 4.5, 7, 11], True, True),
    },
}
for u in BANDS:
    for k in ["pct_above_10dma", "pct_above_20dma", "pct_above_50dma", "pct_above_200dma",
              "pct_10dma_gt_20dma", "pct_20dma_gt_40dma", "pct_50dma_gt_200dma"]:
        BANDS[u][k] = (DMA_BANDS, False, False)
    BANDS[u]["nifty_chg_pct"] = ([-1.5, -0.75, -0.2, 0.2, 0.75, 1.5], False, False)

PCT_FMT = {"pct_above_10dma", "pct_above_20dma", "pct_above_50dma", "pct_above_200dma",
           "pct_10dma_gt_20dma", "pct_20dma_gt_40dma", "pct_50dma_gt_200dma"}


def shade(universe, key, value, n):
    """Return 0..6 colour step, or None when the column is not graded."""
    spec = BANDS.get(universe, {}).get(key)
    if spec is None or value is None or (isinstance(value, float) and np.isnan(value)):
        return None
    bands, as_share, invert = spec
    v = (value / n * 100) if as_share else value
    step = int(np.searchsorted(bands, v, side="right"))
    return 6 - step if invert else step


def regime(row):
    a50, a200 = row.get("pct_above_50dma", np.nan), row.get("pct_above_200dma", np.nan)
    if pd.isna(a50):
        return "insufficient history"
    if a50 >= 60 and a200 >= 50:
        return "broad uptrend"
    if a50 >= 45:
        return "uptrend, narrowing"
    if a50 >= 25:
        return "corrective, mixed participation"
    if a50 >= 12:
        return "distribution / downtrend"
    return "washed out, oversold extreme"


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

    payload, summary = {}, {}
    for u, g in df.groupby("universe"):
        g = g.sort_values("date").reset_index(drop=True)
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
        payload[u] = {"rows": recs, "regime": regime(last),
                      "asof": last["date"].strftime("%d %b %Y"), "sessions": int(len(gg))}
        summary[u] = {"asof": last["date"].strftime("%Y-%m-%d"), "n": int(last["universe_count"]),
                      "regime": regime(last), "a10": last.get("pct_above_10dma"),
                      "a50": last.get("pct_above_50dma"), "a200": last.get("pct_above_200dma"),
                      "net4": int(last["net_4pct"]), "flags": recs[0]["f"]}

    html = (TEMPLATE
            .replace("__DATA__", json.dumps(payload, separators=(",", ":")))
            .replace("__GROUPS__", json.dumps([[gl, [[k, l, lk] for k, l, lk in cs]] for gl, cs in GROUPS]))
            .replace("__LISTS__", json.dumps(lists, separators=(",", ":")))
            .replace("__REPO__", repo or ""))
    with open(out, "w") as f:
        f.write(html)

    v = os.path.join(os.path.dirname(csv), "validation.txt")
    print(f"dashboard -> {out}  ({os.path.getsize(out)//1024} KB)")
    for u, s in summary.items():
        print(f"[{u}] {s['asof']} n={s['n']} | >10DMA {s['a10']}% >50DMA {s['a50']}% "
              f">200DMA {s['a200']}% | net4 {s['net4']:+d} | {s['regime']}"
              + (f" | flags: {', '.join(s['flags'])}" if s["flags"] else ""))
    print("validation:", open(v).read().strip().replace("\n", " / ") if os.path.exists(v) else "not found")


TEMPLATE = r"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Market Breadth</title>
<style>
:root{--paper:#e9ece6;--panel:#f5f7f3;--ink:#16201c;--muted:#5d6b63;--rule:#c8d0c6;
 --s0:#b3402f;--s1:#d17a4e;--s2:#e8b979;--s3:#f2eddc;--s4:#c3d68a;--s5:#87b464;--s6:#3f8a4f}
*{box-sizing:border-box}
body{margin:0;background:var(--paper);color:var(--ink);font:13px/1.4 ui-sans-serif,-apple-system,"Segoe UI",Roboto,sans-serif}
.wrap{max-width:1680px;margin:0 auto;padding:12px 14px 40px}
.top{display:flex;flex-wrap:wrap;gap:10px 18px;align-items:baseline;justify-content:space-between;
 border-bottom:2px solid var(--ink);padding-bottom:7px}
h1{font-size:16px;margin:0;letter-spacing:-.01em;display:inline}
.as{color:var(--muted);font-size:10.5px;letter-spacing:.06em;text-transform:uppercase;margin-left:9px}
.ctl{display:flex;gap:5px;align-items:center}
.tab{padding:3px 12px;border:1px solid var(--rule);background:var(--panel);cursor:pointer;font-size:11.5px;
 font-weight:600;border-radius:2px}
.tab[aria-selected=true]{background:var(--ink);color:var(--paper);border-color:var(--ink)}
select{border:1px solid var(--rule);background:var(--panel);padding:3px 6px;border-radius:2px;font:inherit;font-size:11.5px}
.pills{display:flex;flex-wrap:wrap;gap:0;border:1px solid var(--rule);border-top:0;background:var(--panel);
 margin-bottom:9px}
.pill{padding:5px 13px;border-right:1px solid var(--rule);min-width:96px}
.pill:last-child{border-right:0}
.pill .k{font-size:8.5px;letter-spacing:.1em;text-transform:uppercase;color:var(--muted)}
.pill .v{font:15px/1.2 ui-monospace,"SF Mono",Menlo,monospace;font-variant-numeric:tabular-nums;margin-top:1px}
.pill.reg{min-width:210px}.pill.reg .v{font-family:inherit;font-size:13px;font-weight:700}
.tblwrap{overflow:auto;max-height:80vh;border:1px solid var(--rule);background:var(--panel)}
table{border-collapse:separate;border-spacing:0;width:100%;
 font:11.5px ui-monospace,"SF Mono",Menlo,monospace;font-variant-numeric:tabular-nums}
thead th{position:sticky;background:var(--ink);color:var(--paper);z-index:2}
tr.grp th{top:0;font-size:8.5px;letter-spacing:.13em;text-transform:uppercase;font-weight:600;
 padding:3px 5px;color:#9fb3a6;border-bottom:1px solid #2c3a33}
tr.col th{top:20px;font-size:10px;font-weight:600;padding:4px 5px;text-align:right;white-space:nowrap}
th.gs,td.gs{border-left:2px solid var(--rule)}
tr.grp th.gs{border-left:2px solid #55665c}tr.col th.gs{border-left:2px solid #55665c}
td{padding:2.5px 5px;text-align:right;white-space:nowrap;border-bottom:1px solid #e5e9e1}
td.d,th.d{text-align:left;position:sticky;left:0;background:var(--panel);z-index:1;
 border-right:2px solid var(--rule);font-weight:600;padding-left:7px}
th.d{background:var(--ink);z-index:4}
td.fg{text-align:left;padding:0 4px}
tr:hover td{background:#e2e8dd!important}
.fl{display:inline-block;font-size:8px;padding:1px 3px;border-radius:2px;margin-right:2px;
 letter-spacing:.05em;text-transform:uppercase;font-family:ui-sans-serif,sans-serif}
.fl.bear{background:var(--s0);color:#fff}.fl.bull{background:var(--s6);color:#fff}
.fl.thrust{background:var(--ink);color:var(--paper)}
td.lk{cursor:pointer}td.lk:hover{outline:1.5px solid var(--ink);outline-offset:-1.5px}
.legend{display:flex;gap:12px;align-items:center;margin-top:8px;color:var(--muted);font-size:10.5px;flex-wrap:wrap}
.sc{display:flex}.sc i{width:19px;height:10px;display:block}
footer{margin-top:9px;color:var(--muted);font-size:10.5px;line-height:1.55}
#ov{position:fixed;inset:0;background:rgba(20,28,24,.45);display:none;align-items:center;justify-content:center;z-index:9}
#ov.on{display:flex}
#bx{background:var(--panel);border:1px solid var(--ink);max-width:720px;max-height:76vh;overflow:auto;padding:16px 18px}
#bx h3{margin:0 0 3px;font-size:14px}#bx .sub{color:var(--muted);font-size:11px;margin-bottom:10px}
#bx .syms{font:11.5px ui-monospace,Menlo,monospace;columns:4;column-gap:18px;line-height:1.75}
#bx button{margin-top:12px;border:1px solid var(--ink);background:var(--ink);color:var(--paper);
 padding:4px 12px;cursor:pointer;font:inherit;font-size:11.5px}
@media(max-width:760px){.pill{min-width:78px}.pill.reg{min-width:100%}}
</style></head><body><div class="wrap">
<div class="top"><div><h1>Market Breadth</h1><span class="as" id="as"></span></div>
<div class="ctl"><div class="tab" data-u="ALL" aria-selected="true">All NSE</div>
<div class="tab" data-u="FNO" aria-selected="false">F&amp;O</div>
<select id="rows"><option value="60">60</option><option value="120">120</option>
<option value="250" selected>250</option><option value="0">All</option></select></div></div>
<div class="pills" id="pills"></div>
<div class="tblwrap"><table id="t"><thead></thead><tbody></tbody></table></div>
<div class="legend"><span>Fixed thresholds, not percentiles</span>
<span class="sc"><i style="background:var(--s0)"></i><i style="background:var(--s1)"></i><i style="background:var(--s2)"></i>
<i style="background:var(--s3)"></i><i style="background:var(--s4)"></i><i style="background:var(--s5)"></i>
<i style="background:var(--s6)"></i></span><span>bearish &rarr; bullish. Counts are graded as a share of that day's universe</span>
<span>&middot; shaded cells with a hover outline open the stock list</span></div>
<footer id="ft"></footer></div>
<div id="ov"><div id="bx"><h3 id="bh"></h3><div class="sub" id="bs"></div><div class="syms" id="by"></div>
<button onclick="document.getElementById('ov').classList.remove('on')">Close</button></div></div>
<script>
const DATA=__DATA__, GROUPS=__GROUPS__, LISTS=__LISTS__, REPO="__REPO__";
const LBL={up4:"up 4% or more",dn4:"down 4% or more",hi52:"at a 52-week high",
 lo52:"at a 52-week low",up25:"up 25% or more in 21 sessions",dn25:"down 25% or more in 21 sessions"};
let U="ALL", N=250;
const fmt=(k,v)=>v==null?"":k==="nifty_close"?v.toLocaleString("en-IN",{maximumFractionDigits:0})
 :(k.startsWith("pct_")?v.toFixed(1):k==="nifty_chg_pct"?v.toFixed(2):v);

function pills(d){const r=d.rows[0],p=[];
 p.push(`<div class="pill reg"><div class="k">Regime &middot; ${U==="ALL"?"All NSE":"F&O"}</div><div class="v">${d.regime}</div></div>`);
 [["pct_above_10dma","&gt;10 DMA"],["pct_above_50dma","&gt;50 DMA"],["pct_above_200dma","&gt;200 DMA"]]
  .forEach(([k,l])=>p.push(`<div class="pill"><div class="k">${l}</div><div class="v">${
   r.v[k]==null?"—":r.v[k].toFixed(1)+"%"}</div></div>`));
 const n4=r.v.net_4pct;
 p.push(`<div class="pill"><div class="k">Net 4%</div><div class="v" style="color:${n4>0?"var(--s6)":"var(--s0)"}">${n4>0?"+":""}${n4}</div></div>`);
 p.push(`<div class="pill"><div class="k">Universe</div><div class="v">${r.n}</div></div>`);
 if(r.f.length)p.push(`<div class="pill"><div class="k">Flags</div><div class="v">${
  r.f.map(f=>`<span class="fl ${f}">${f}</span>`).join("")}</div></div>`);
 document.getElementById("pills").innerHTML=p.join("");
 document.getElementById("as").textContent=`as of ${d.asof} · ${r.n} stocks · ${d.sessions} sessions`;}

function head(){const g=[],c=[];
 g.push('<th class="d" rowspan="2">Date</th><th rowspan="2"></th>');
 GROUPS.forEach(([gl,cs],i)=>{const s=i?' gs':'';
  g.push(`<th colspan="${cs.length}" class="${s.trim()}">${gl}</th>`);
  cs.forEach(([k,l],j)=>c.push(`<th class="${!j&&i?'gs':''}">${l}</th>`));});
 document.querySelector("#t thead").innerHTML=`<tr class="grp">${g.join("")}</tr><tr class="col">${c.join("")}</tr>`;}

function body(d){const rows=(N?d.rows.slice(0,N):d.rows).map(r=>{
 const tds=[];GROUPS.forEach(([gl,cs],i)=>cs.forEach(([k,l,lk],j)=>{
  const s=r.c[k],bg=s==null?"":`background:var(--s${s})`;
  const cl=(!j&&i?"gs ":"")+(lk&&r.v[k]>0?"lk":"");
  tds.push(`<td class="${cl.trim()}" style="${bg}"${lk&&r.v[k]>0?` data-i="${r.iso}" data-k="${lk}"`:""}>${fmt(k,r.v[k])}</td>`);}));
 return `<tr><td class="d">${r.d} ${r.wd}</td><td class="fg">${
  r.f.map(f=>`<span class="fl ${f}">${f}</span>`).join("")}</td>${tds.join("")}</tr>`;});
 document.querySelector("#t tbody").innerHTML=rows.join("");
 document.querySelectorAll("td.lk").forEach(td=>td.onclick=()=>show(td.dataset.i,td.dataset.k));}

async function show(iso,k){const bx=document.getElementById("ov");
 document.getElementById("bh").textContent=`${U==="ALL"?"All NSE":"F&O"} · ${LBL[k]}`;
 document.getElementById("bs").textContent=iso;
 document.getElementById("by").textContent="loading...";bx.classList.add("on");
 let L=LISTS[iso];
 if(!L&&REPO){try{const r=await fetch(`https://raw.githubusercontent.com/${REPO}/main/data/lists/${iso}.json`);
  if(r.ok)L=await r.json();}catch(e){}}
 const s=L&&L[U]&&L[U][k];
 document.getElementById("by").textContent=s&&s.length?s.join("   ")
  :"Not available. Symbol lists are kept for the last 90 sessions only.";
 document.getElementById("bs").textContent=`${iso}${s?" · "+s.length+" stocks":""}`;}

function draw(){const d=DATA[U];if(!d)return;pills(d);body(d);}
document.querySelectorAll(".tab").forEach(t=>t.onclick=()=>{
 document.querySelectorAll(".tab").forEach(x=>x.setAttribute("aria-selected",x===t));U=t.dataset.u;draw();});
document.getElementById("rows").onchange=e=>{N=+e.target.value;draw();};
document.getElementById("ov").onclick=e=>{if(e.target.id==="ov")e.currentTarget.classList.remove("on");};
document.getElementById("ft").innerHTML="Source: NSE UDiFF bhavcopy, series EQ. Prices chained on close over previous close, so splits and bonuses are adjusted. F&amp;O universe is point-in-time from the derivatives bhavcopy. 4% moves are close to close; 25% over 21 sessions; 20% over 5 sessions. Divergence flags mark a 20-day Nifty extreme that % above 50 DMA does not confirm. Thrust marks a session with 10% or more of the universe up 4% and at least 3 times as many risers as fallers.";
head();draw();
</script></body></html>"""


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", default="data/breadth_history.csv")
    ap.add_argument("--out", default="dashboard.html")
    ap.add_argument("--rows", type=int, default=250)
    ap.add_argument("--repo", default="bobbythomas-create/market-breadth")
    a = ap.parse_args()
    build(a.csv, a.out, a.rows, a.repo)
