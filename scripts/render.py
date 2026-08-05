#!/usr/bin/env python3
"""
Render breadth_history.csv into a self-contained HTML dashboard.

Usage
  python render.py --csv data/breadth_history.csv --out dashboard.html [--rows 120]

Prints a compact text summary to stdout (the only thing that should ever enter
the model's context). Never print the table.
"""

import argparse
import json
import os
import numpy as np
import pandas as pd

# metric key -> (label, group, direction) ; direction 1 = high is bullish, -1 = inverted
METRICS = [
    ("advances",            "Adv",            "participation",  1),
    ("declines",            "Dec",            "participation", -1),
    ("up_4pct",             "Up 4%",          "momentum",       1),
    ("down_4pct",           "Dn 4%",          "momentum",      -1),
    ("net_4pct",            "Net 4%",         "momentum",       1),
    ("up_25pct_21d",        "Up 25% 1M",      "thrust",         1),
    ("down_25pct_21d",      "Dn 25% 1M",      "thrust",        -1),
    ("up_50pct_21d",        "Up 50% 1M",      "thrust",         1),
    ("down_50pct_21d",      "Dn 50% 1M",      "thrust",        -1),
    ("up_20pct_5d",         "Up 20% 5D",      "thrust",         1),
    ("down_20pct_5d",       "Dn 20% 5D",      "thrust",        -1),
    ("pct_above_10dma",     "% >10DMA",       "trend",          1),
    ("pct_above_20dma",     "% >20DMA",       "trend",          1),
    ("pct_above_40dma",     "% >40DMA",       "trend",          1),
    ("pct_above_50dma",     "% >50DMA",       "trend",          1),
    ("pct_above_200dma",    "% >200DMA",      "trend",          1),
    ("pct_10dma_gt_20dma",  "10>20DMA",       "structure",      1),
    ("pct_20dma_gt_40dma",  "20>40DMA",       "structure",      1),
    ("pct_50dma_gt_200dma", "50>200DMA",      "structure",      1),
    ("new_52w_high",        "52w H",          "extremes",       1),
    ("new_52w_low",         "52w L",          "extremes",      -1),
    ("nifty_close",         "Nifty",          "price",          0),
    ("nifty_chg_pct",       "Chg %",          "price",          1),
]
PCT_KEYS = {k for k, _, _, _ in METRICS if k.startswith("pct_")}


def percentile_grade(df: pd.DataFrame) -> dict:
    """Colour value = trailing-250-session percentile rank, direction-adjusted."""
    g = {}
    for key, _, _, direction in METRICS:
        if direction == 0 or key not in df:
            continue
        s = pd.to_numeric(df[key], errors="coerce")
        pr = s.rolling(250, min_periods=40).apply(lambda w: (w.iloc[-1] >= w).mean(), raw=False)
        if direction < 0:
            pr = 1 - pr
        g[key] = [None if pd.isna(v) else round(float(v), 3) for v in pr]
    return g


def regime(row) -> tuple:
    """Coarse regime read from the two most reliable participation series."""
    a40 = row.get("pct_above_40dma", np.nan)
    a200 = row.get("pct_above_200dma", np.nan)
    if pd.isna(a40):
        return ("insufficient history", "neutral")
    if a40 >= 60 and a200 >= 50:
        return ("broad uptrend", "bull")
    if a40 >= 45:
        return ("uptrend, narrowing", "mild")
    if a40 >= 25:
        return ("corrective, mixed participation", "neutral")
    if a40 >= 12:
        return ("distribution / downtrend", "bear")
    return ("washed out, oversold extreme", "extreme")


def build(csv, out, rows):
    df = pd.read_csv(csv)
    df["date"] = pd.to_datetime(df["date"])
    payload, summary = {}, {}
    for u, g in df.groupby("universe"):
        g = g.sort_values("date").reset_index(drop=True)
        grades = percentile_grade(g)
        g = g.iloc[-rows:] if rows else g
        sl = slice(len(grades[next(iter(grades))]) - len(g), None) if grades else slice(None)
        recs = []
        for i, (_, r) in enumerate(g.iterrows()):
            rec = {"date": r["date"].strftime("%Y-%m-%d"),
                   "day": r["date"].strftime("%a"),
                   "n": int(r["universe_count"]),
                   "flags": [f for f, k in [("bear div", "div_bearish"),
                                            ("bull div", "div_bullish"),
                                            ("thrust", "thrust")]
                             if bool(r.get(k, False))]}
            for key, _, _, _ in METRICS:
                v = r.get(key, np.nan)
                rec[key] = None if pd.isna(v) else (round(float(v), 2) if key in PCT_KEYS or key in
                                                    ("nifty_close", "nifty_chg_pct") else int(v))
            rec["g"] = {k: (vals[sl][i] if i < len(vals[sl]) else None) for k, vals in grades.items()}
            recs.append(rec)
        recs.reverse()                                    # newest first
        last = g.iloc[-1]
        txt, tone = regime(last)
        payload[u] = {"rows": recs, "regime": txt, "tone": tone,
                      "asof": last["date"].strftime("%d %b %Y"),
                      "sessions": int(len(g)),
                      "spark": [None if pd.isna(v) else float(v) for v in g["net_4pct"].tail(60)],
                      "nifty": [None if pd.isna(v) else float(v) for v in g["nifty_close"].tail(60)]}
        summary[u] = {"asof": last["date"].strftime("%Y-%m-%d"), "n": int(last["universe_count"]),
                      "regime": txt,
                      "a10": last.get("pct_above_10dma"), "a40": last.get("pct_above_40dma"),
                      "a200": last.get("pct_above_200dma"),
                      "net4": int(last["net_4pct"]), "flags": recs[0]["flags"]}

    html = TEMPLATE.replace("__DATA__", json.dumps(payload, separators=(",", ":")))
    html = html.replace("__METRICS__", json.dumps([[k, l, gp] for k, l, gp, _ in METRICS]))
    with open(out, "w") as f:
        f.write(html)

    vpath = os.path.join(os.path.dirname(csv), "validation.txt")
    v = open(vpath).read().strip() if os.path.exists(vpath) else "validation file not found"
    print(f"dashboard -> {out}")
    for u, s in summary.items():
        print(f"[{u}] {s['asof']} n={s['n']} | >10DMA {s['a10']}% >40DMA {s['a40']}% "
              f">200DMA {s['a200']}% | net4 {s['net4']:+d} | {s['regime']}"
              + (f" | flags: {', '.join(s['flags'])}" if s["flags"] else ""))
    print("validation:", v.replace("\n", " / "))


TEMPLATE = r"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Market Breadth</title>
<style>
:root{
  --paper:#e9ece6; --panel:#f5f7f3; --ink:#16201c; --muted:#5d6b63; --rule:#c8d0c6;
  --neg3:#b3402f; --neg2:#d17a4e; --neg1:#e8b979; --mid:#f2eddc;
  --pos1:#c3d68a; --pos2:#87b464; --pos3:#3f8a4f;
}
*{box-sizing:border-box}
body{margin:0;background:var(--paper);color:var(--ink);
 font-family:ui-sans-serif,-apple-system,"Segoe UI",Roboto,sans-serif;font-size:13px}
.wrap{max-width:1560px;margin:0 auto;padding:20px 16px 60px}
h1{font-size:26px;letter-spacing:-.02em;margin:0;font-weight:700}
.sub{color:var(--muted);font-size:12px;margin-top:3px;letter-spacing:.04em;text-transform:uppercase}
header{display:flex;flex-wrap:wrap;gap:16px;align-items:flex-end;justify-content:space-between;
 border-bottom:2px solid var(--ink);padding-bottom:12px;margin-bottom:16px}
.tabs{display:flex;gap:4px}
.tab{padding:7px 16px;border:1px solid var(--rule);background:var(--panel);cursor:pointer;
 font-weight:600;letter-spacing:.03em;border-radius:2px}
.tab[aria-selected=true]{background:var(--ink);color:var(--paper);border-color:var(--ink)}
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px;margin-bottom:16px}
.card{background:var(--panel);border:1px solid var(--rule);border-radius:2px;padding:10px 12px}
.card .k{font-size:10px;letter-spacing:.09em;text-transform:uppercase;color:var(--muted)}
.card .v{font-family:ui-monospace,"SF Mono",Menlo,monospace;font-size:24px;font-variant-numeric:tabular-nums;
 margin-top:3px;letter-spacing:-.02em}
.regime{grid-column:span 2}
.regime .v{font-family:inherit;font-size:17px;line-height:1.25;font-weight:600}
.spine{display:flex;align-items:flex-end;gap:1px;height:54px;background:var(--panel);
 border:1px solid var(--rule);padding:6px;margin-bottom:16px;position:relative}
.spine .b{flex:1;min-width:2px;position:relative}
.spine .b i{position:absolute;left:0;right:0;display:block}
.spine .mid{position:absolute;left:6px;right:6px;top:50%;border-top:1px dashed var(--rule)}
.spine .cap{position:absolute;right:8px;top:4px;font-size:10px;color:var(--muted);
 letter-spacing:.08em;text-transform:uppercase}
.tblwrap{overflow:auto;max-height:74vh;border:1px solid var(--rule);background:var(--panel)}
table{border-collapse:separate;border-spacing:0;width:100%;
 font-family:ui-monospace,"SF Mono",Menlo,monospace;font-variant-numeric:tabular-nums;font-size:12px}
th{position:sticky;top:0;background:var(--ink);color:var(--paper);font-weight:600;
 padding:7px 6px;text-align:right;white-space:nowrap;font-size:10.5px;letter-spacing:.03em;z-index:2}
th.g{border-left:1px solid #3c4a42}
td{padding:4px 6px;text-align:right;white-space:nowrap;border-bottom:1px solid #e2e6de}
td.d,th.d{text-align:left;position:sticky;left:0;background:var(--panel);z-index:1;
 border-right:1px solid var(--rule);font-weight:600}
th.d{background:var(--ink);z-index:3}
tr:hover td{background:#e4eadf}
tr:hover td.d{background:#e4eadf}
.fl{display:inline-block;font-size:9px;padding:1px 4px;border-radius:2px;margin-left:4px;
 letter-spacing:.05em;text-transform:uppercase;vertical-align:1px}
.fl.bear{background:var(--neg3);color:#fff}.fl.bull{background:var(--pos3);color:#fff}
.fl.thr{background:var(--ink);color:var(--paper)}
.legend{display:flex;gap:14px;align-items:center;margin:12px 0 0;color:var(--muted);font-size:11px;flex-wrap:wrap}
.scale{display:flex}.scale i{width:22px;height:11px;display:block}
.ctl{display:flex;gap:8px;align-items:center}
select{border:1px solid var(--rule);background:var(--panel);padding:5px 8px;border-radius:2px;font:inherit}
footer{margin-top:14px;color:var(--muted);font-size:11px;line-height:1.6}
@media(max-width:700px){.regime{grid-column:span 2}h1{font-size:20px}}
</style></head><body><div class="wrap">
<header>
  <div><h1>Market Breadth</h1><div class="sub" id="asof"></div></div>
  <div class="ctl">
    <div class="tabs" role="tablist">
      <div class="tab" role="tab" data-u="ALL" aria-selected="true">All NSE</div>
      <div class="tab" role="tab" data-u="FNO" aria-selected="false">F&amp;O</div>
    </div>
    <select id="rows"><option value="60">60 sessions</option><option value="120" selected>120</option>
    <option value="250">250</option><option value="0">All</option></select>
  </div>
</header>
<div class="cards" id="cards"></div>
<div class="spine" id="spine"><div class="mid"></div><div class="cap">net 4% movers, last 60 sessions</div></div>
<div class="tblwrap"><table id="tbl"><thead></thead><tbody></tbody></table></div>
<div class="legend"><span>Colour = percentile vs trailing 250 sessions</span>
<span class="scale"><i style="background:var(--neg3)"></i><i style="background:var(--neg2)"></i>
<i style="background:var(--neg1)"></i><i style="background:var(--mid)"></i>
<i style="background:var(--pos1)"></i><i style="background:var(--pos2)"></i><i style="background:var(--pos3)"></i></span>
<span>weak &rarr; strong (down-metrics inverted, so red always means bearish)</span></div>
<footer id="foot"></footer>
</div>
<script>
const DATA=__DATA__, METRICS=__METRICS__;
const SCALE=['--neg3','--neg2','--neg1','--mid','--pos1','--pos2','--pos3'];
let U='ALL', ROWS=120;
const col=p=>p==null?'transparent':`var(${SCALE[Math.min(6,Math.floor(p*7))]})`;
const fmt=(k,v)=>v==null?'':(k==='nifty_close'?v.toLocaleString('en-IN',{maximumFractionDigits:0})
  :(k.startsWith('pct_')||k==='nifty_chg_pct')?v.toFixed(k==='nifty_chg_pct'?2:1):v);

function cards(d){
  const r=d.rows[0], c=[];
  c.push(`<div class="card regime"><div class="k">Regime &middot; ${U==='ALL'?'All NSE':'F&O'}</div>
    <div class="v">${d.regime}</div></div>`);
  [['pct_above_10dma','% above 10 DMA'],['pct_above_40dma','% above 40 DMA'],
   ['pct_above_200dma','% above 200 DMA'],['net_4pct','Net 4% movers'],
   ['universe_count','Universe']].forEach(([k,l])=>{
    const v=k==='universe_count'?r.n:r[k];
    c.push(`<div class="card"><div class="k">${l}</div><div class="v" style="color:${
      k==='net_4pct'?(v>0?'var(--pos3)':'var(--neg3)'):'inherit'}">${
      v==null?'—':(k.startsWith('pct_')?v.toFixed(1)+'%':(k==='net_4pct'&&v>0?'+':'')+v)}</div></div>`);
  });
  document.getElementById('cards').innerHTML=c.join('');
  document.getElementById('asof').textContent=`as of ${d.asof} · ${r.n} stocks · ${d.sessions} sessions loaded`;
}
function spine(d){
  const s=d.spark.slice(), m=Math.max(1,...s.map(v=>Math.abs(v||0)));
  const el=document.getElementById('spine');
  el.querySelectorAll('.b').forEach(n=>n.remove());
  s.forEach(v=>{const b=document.createElement('div');b.className='b';
    const h=Math.abs(v||0)/m*46, up=(v||0)>=0;
    b.innerHTML=`<i style="height:${h}px;background:${up?'var(--pos2)':'var(--neg2)'};
      ${up?'bottom:50%':'top:50%'};position:absolute"></i>`;
    b.title=`${v>0?'+':''}${v}`; el.appendChild(b);});
}
function table(d){
  const th=['<th class="d">Date</th>'];let last='';
  METRICS.forEach(([k,l,g])=>{const n=g!==last;last=g;th.push(`<th class="${n?'g':''}">${l}</th>`)});
  document.querySelector('#tbl thead').innerHTML='<tr>'+th.join('')+'</tr>';
  const rows=(ROWS?d.rows.slice(0,ROWS):d.rows).map(r=>{
    const fl=r.flags.map(f=>`<span class="fl ${f==='bear div'?'bear':f==='bull div'?'bull':'thr'}">${f}</span>`).join('');
    const tds=METRICS.map(([k])=>{
      const g=r.g[k];
      return `<td style="background:${col(g)}">${fmt(k,r[k])}</td>`;});
    return `<tr><td class="d">${r.date.slice(5)} ${r.day}${fl}</td>${tds.join('')}</tr>`;});
  document.querySelector('#tbl tbody').innerHTML=rows.join('');
}
function draw(){const d=DATA[U];if(!d){document.getElementById('cards').innerHTML=
  '<div class="card regime"><div class="v">No data for this universe</div></div>';
  document.querySelector('#tbl tbody').innerHTML='';return;}
  cards(d);spine(d);table(d);}
document.querySelectorAll('.tab').forEach(t=>t.onclick=()=>{
  document.querySelectorAll('.tab').forEach(x=>x.setAttribute('aria-selected',x===t));
  U=t.dataset.u;draw();});
document.getElementById('rows').onchange=e=>{ROWS=+e.target.value;draw();};
document.getElementById('foot').innerHTML=
 'Source: NSE UDiFF bhavcopy, series EQ. Prices chained on close/prev-close so splits and bonuses are adjusted. '+
 'F&amp;O universe is point-in-time from the derivatives bhavcopy. 4% moves are close-to-close. '+
 '25%/50% moves are over 21 sessions, 20% over 5 sessions. Divergence flags mark a 20-day Nifty extreme '+
 'that % above 40 DMA does not confirm.';
draw();
</script></body></html>"""


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", default="data/breadth_history.csv")
    ap.add_argument("--out", default="dashboard.html")
    ap.add_argument("--rows", type=int, default=250)
    a = ap.parse_args()
    build(a.csv, a.out, a.rows)
