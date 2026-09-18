#!/usr/bin/env python3
"""
Opportunity engine, run AFTER ingest.py each session.

Turns the price store into a trader's actionable list across the single-stock F&O
universe (plus an index block), surfacing LONGS, SHORTS and FADES with equal
weight. Every idea is a 2-way possibility: the tape decides direction, this ranks
where strength, volume and momentum line up. Nothing here is a buy/sell
instruction; conviction still needs your chart and option chain at the trade.

Signal classes
  LONG  : momentum burst, RS leader, Stage-2 breakout, range-expansion up, episodic pivot
  SHORT : breakdown, RS laggard, Stage-4 breakdown, range-expansion down, gap-down
  FADE  : mean-reversion after an expansion, both directions
          - overbought fade (short): stretched >=3 ATR above the 20 DMA after a
            fast run, now showing an exhaustion bar
          - oversold bounce (long): stretched <=3 ATR below the 20 DMA after a
            fast drop, now showing a reversal bar

Conviction = move size x volume confirmation x trend alignment (RS-tilted).
Trend-following ideas score full alignment; fades are counter-trend and are
capped lower and clearly tagged.

Missed a day or two? Each run also writes a dated snapshot under
data/opportunities/ (last 120 kept), and opportunities.json carries the list of
available dates so the dashboard can load a prior session on demand. Ideas are
tagged NEW (fired today) vs CONT (also present in the prior snapshot), so nothing
that fired while you were away is lost.

Reads : data/prices.parquet, data/fno_universe.parquet, data/constituents.json,
        data/index_ohlc.parquet
Writes: data/opportunities.json  and  data/opportunities/<date>.json
"""

import glob
import json
import os

import numpy as np
import pandas as pd

DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data")

RS_PERIODS = [(63, 0.40), (126, 0.20), (189, 0.20), (252, 0.20)]
KEEP_SNAPSHOTS = 120
TOP_N = 30                      # cap per bucket, keeps the json small

# thresholds (India-calibrated; see reference tab)
BURST = 0.04                    # single-day move that counts as conviction
BURST_STRONG = 0.06
VOL_MULT = 1.5                  # today's volume vs 20-day average
VOL_STRONG = 2.0
GAP = 0.05                      # episodic pivot / gap-down gap size
EXT_ATR = 2.5                   # ATR units from the 20 DMA to call it "stretched" (India-calibrated)
RUN_ATR = 4.0                   # 10-day move as ATR-multiples (regime-robust, replaces a fixed %)


def _load(name, cols=None):
    p = os.path.join(DATA, name)
    if os.path.exists(p):
        return pd.read_parquet(p)
    return pd.DataFrame(columns=cols or [])


def adjusted_panel(prices):
    p = prices.sort_values(["symbol", "date"]).copy()
    p["ret"] = p["close"] / p["prev_close"] - 1.0
    p.loc[p["ret"].abs() > 0.85, "ret"] = np.nan
    p["ret"] = p["ret"].fillna(0.0)
    p["adj"] = p.groupby("symbol")["ret"].transform(lambda s: (1 + s).cumprod())
    return p


def rs_universe(p):
    """IBD-style weighted RS raw score per symbol, then a 1-99 percentile across
    the whole universe. Computed on the adjusted panel so splits do not distort."""
    adj = p.pivot(index="date", columns="symbol", values="adj").sort_index()
    last = adj.iloc[-1]
    score = pd.Series(0.0, index=adj.columns)
    wsum = 0.0
    for n, w in RS_PERIODS:
        if len(adj) > n:
            r = last / adj.iloc[-1 - n] - 1.0
            score = score.add(r * w, fill_value=np.nan)
            wsum += w
    if wsum:
        score = score / wsum
    valid = score.dropna()
    if len(valid) < 20:
        return pd.Series(np.nan, index=adj.columns)
    return (valid.rank(pct=True) * 98 + 1).reindex(adj.columns)


def stage_of(price, ma150, ma150_prev):
    if np.isnan(ma150) or np.isnan(price):
        return "?"
    rising = (not np.isnan(ma150_prev)) and ma150 > ma150_prev
    if price > ma150 and rising:
        return "2"
    if price > ma150 and not rising:
        return "1"
    if price < ma150 and rising:
        return "3"
    return "4"


def per_stock(sub):
    """sub = one symbol's rows, sorted by date, with adj + raw OHLCV. Returns the
    latest-session feature dict, or None if too little history."""
    if len(sub) < 160:
        return None
    adj = sub["adj"].values
    close = sub["close"].values
    high = sub["high"].values
    low = sub["low"].values
    vol = sub["volume"].values.astype(float)
    prev = sub["prev_close"].values
    op = sub["open"].values

    def ma(w):
        return adj[-w:].mean() if len(adj) >= w else np.nan
    ma20, ma50, ma150, ma200 = ma(20), ma(50), ma(150), ma(200)
    ma150_prev = adj[-171:-21].mean() if len(adj) >= 171 else np.nan
    if np.isnan(ma200):
        return None

    a = adj[-1]
    ret1 = close[-1] / prev[-1] - 1 if prev[-1] else np.nan
    gap = op[-1] / prev[-1] - 1 if prev[-1] else np.nan
    vol20 = np.nanmean(vol[-21:-1]) if len(vol) >= 21 else np.nan
    volmult = vol[-1] / vol20 if vol20 and vol20 > 0 else np.nan
    ret10 = adj[-1] / adj[-11] - 1 if len(adj) >= 11 else np.nan

    # ATR% (14) on raw OHLC
    tr = np.maximum.reduce([high[1:] - low[1:],
                            np.abs(high[1:] - close[:-1]),
                            np.abs(low[1:] - close[:-1])])
    atr_pct = (tr[-14:].mean() / close[-1] * 100) if len(tr) >= 14 and close[-1] else np.nan

    hi52 = adj[-252:].max() if len(adj) >= 200 else np.nan
    lo52 = adj[-252:].min() if len(adj) >= 200 else np.nan
    from_high = (a / hi52 - 1) * 100 if hi52 else np.nan
    from_low = (a / lo52 - 1) * 100 if lo52 else np.nan

    ext_pct = (a / ma20 - 1) * 100 if ma20 else np.nan       # % from 20 DMA
    ext_atr = ext_pct / atr_pct if atr_pct and not np.isnan(atr_pct) else np.nan

    rng = high[-1] - low[-1]
    close_pos = (close[-1] - low[-1]) / rng if rng > 0 else 0.5   # 1=near high, 0=near low
    up_stack = (ma50 > ma150 > ma200)
    down_stack = (ma50 < ma150 < ma200)
    stage = stage_of(a, ma150, ma150_prev)

    return dict(ret1=ret1, gap=gap, volmult=volmult, ret10=ret10, atr_pct=atr_pct,
                from_high=from_high, from_low=from_low, ext_atr=ext_atr,
                close_pos=close_pos, up_stack=up_stack, down_stack=down_stack,
                stage=stage, above20=a > ma20, above50=a > ma50)


def classify(sym, f, rs):
    """Return (side, classes[], conviction 0-1, reason) or None."""
    ret1, vm = f["ret1"], f["volmult"]
    vm = vm if not (vm is None or np.isnan(vm)) else 1.0
    longs, shorts = [], []

    # LONG classes
    if ret1 >= BURST and vm >= VOL_MULT and f["above20"] and f["above50"]:
        longs.append("burst")
    if not np.isnan(rs) and rs >= 90 and f["stage"] in ("1", "2") and f["above50"]:
        longs.append("RS leader")
    if f["stage"] == "2" and f["from_high"] >= -5 and f["up_stack"]:
        longs.append("Stage-2 break")
    if f["gap"] >= GAP and vm >= VOL_STRONG and ret1 > 0:
        longs.append("episodic pivot")
    # SHORT classes
    if ret1 <= -BURST and vm >= VOL_MULT and (not f["above20"]) and (not f["above50"]):
        shorts.append("breakdown")
    if not np.isnan(rs) and rs <= 10 and f["stage"] == "4" and not f["above50"]:
        shorts.append("RS laggard")
    if f["stage"] == "4" and f["from_low"] <= 5 and f["down_stack"]:
        shorts.append("Stage-4 break")
    if f["gap"] <= -GAP and vm >= VOL_STRONG and ret1 < 0:
        shorts.append("gap-down")

    # FADE classes (counter-trend, mean reversion after an expansion)
    fade = None
    run_atr = (f["ret10"] * 100 / f["atr_pct"]) if (f["atr_pct"] and not np.isnan(f["atr_pct"])) else 0.0
    if (not np.isnan(f["ext_atr"])) and f["ext_atr"] >= EXT_ATR and run_atr >= RUN_ATR \
            and (ret1 < 0 or f["close_pos"] <= 0.4):
        fade = ("FADE-S", ["overbought fade"],
                f"+{f['ext_atr']:.1f} ATR extended after +{f['ret10']*100:.0f}% run, exhaustion bar")
    elif (not np.isnan(f["ext_atr"])) and f["ext_atr"] <= -EXT_ATR and run_atr <= -RUN_ATR \
            and (ret1 > 0 or f["close_pos"] >= 0.6):
        fade = ("FADE-L", ["oversold bounce"],
                f"{f['ext_atr']:.1f} ATR below 20 DMA after {f['ret10']*100:.0f}% drop, reversal bar")

    MOM = {"burst", "breakdown", "episodic pivot", "gap-down"}

    def tagof(s):
        return "High" if s >= 0.68 else "Med" if s >= 0.45 else "Low"

    def why_of(cls, side):
        primary = cls[0]
        rs_s = f", RS {int(rs)}" if not np.isnan(rs) else ""
        stg = f["stage"]
        if primary in MOM:
            base = f"{primary} {ret1*100:+.1f}% on {vm:.1f}x vol{rs_s}"
            if (side == "LONG" and stg in ("1", "2")) or (side == "SHORT" and stg == "4"):
                base += f", Stage {stg}"
            return base
        # structural setup: lead with the structure, note today's move as context
        return f"{primary}, Stage {stg}{rs_s}, today {ret1*100:+.1f}% on {vm:.1f}x vol"

    def rec(side, cls, move_c):
        vc = min(vm / VOL_STRONG, 1.0)
        align = 1.0 if side in ("LONG", "SHORT") else 0.4
        if side == "LONG":
            rs_c = (rs / 99) if not np.isnan(rs) else 0.5
        elif side == "SHORT":
            rs_c = (1 - rs / 99) if not np.isnan(rs) else 0.5
        else:
            rs_c = 0.5
        s = round(0.35 * move_c + 0.25 * vc + 0.25 * align + 0.15 * rs_c, 3)
        return dict(s=sym, side=side, cls=cls, ret1=round(ret1 * 100, 1),
                    vm=round(vm, 1), rs=None if np.isnan(rs) else int(rs),
                    stage=f["stage"], conv=s, tag=tagof(s), why=why_of(cls, side))

    out = []
    if longs:   # reward only up-moves for a long
        out.append(rec("LONG", longs, min(max(ret1, 0) / BURST_STRONG, 1.0)))
    if shorts:  # reward only down-moves for a short
        out.append(rec("SHORT", shorts, min(max(-ret1, 0) / BURST_STRONG, 1.0)))
    if fade:
        side, cls, why = fade
        r = rec(side, cls, min(abs(f["ext_atr"]) / 5.0, 1.0))
        r["why"] = why
        out.append(r)
    return out


def index_block(oh):
    """Trend + range state for the tradable index underlyings."""
    want = {"NIFTY50": "Nifty 50", "NIFTYBANK": "Bank Nifty", "BANKNIFTY": "Bank Nifty", "NIFTYIT": "Nifty IT"}
    out = []
    if not len(oh):
        return out
    oh = oh.copy(); oh["date"] = pd.to_datetime(oh["date"])
    for tag, label in want.items():
        g = oh[oh["index"] == tag].sort_values("date")
        if len(g) < 205:
            continue
        c = g["close"].values
        ma50, ma200 = c[-50:].mean(), c[-200:].mean()
        ret1 = c[-1] / c[-2] - 1 if len(c) >= 2 else np.nan
        h, l, cl = g["high"].values, g["low"].values, g["close"].values
        tr = np.maximum.reduce([h[1:] - l[1:], np.abs(h[1:] - cl[:-1]), np.abs(l[1:] - cl[:-1])])
        atr = pd.Series(tr).rolling(14).mean().values
        atr_pctile = (atr[-1] <= np.nanpercentile(atr[-100:], 20)) if len(atr) >= 100 else False
        atr_hi = (atr[-1] >= np.nanpercentile(atr[-100:], 80)) if len(atr) >= 100 else False
        trend = "up" if c[-1] > ma50 > ma200 else "down" if c[-1] < ma50 < ma200 else "mixed"
        state = "coiled" if atr_pctile else "expanding" if atr_hi else "normal"
        posture = ("trend up, buy dips / bull structures" if trend == "up"
                   else "trend down, sell rallies / bear structures" if trend == "down"
                   else "no clean trend, range / neutral")
        out.append(dict(tag=tag, label=label, ret1=round(float(ret1) * 100, 2),
                        trend=trend, vol_state=state, posture=posture,
                        note=("coiled, expect a range expansion soon" if state == "coiled"
                              else "volatility elevated" if state == "expanding" else "")))
    # de-dup Bank Nifty if both aliases present
    seen, uniq = set(), []
    for r in out:
        if r["label"] in seen:
            continue
        seen.add(r["label"]); uniq.append(r)
    return uniq


def build():
    prices = _load("prices.parquet")
    if not len(prices):
        print("no prices.parquet"); return
    fno = _load("fno_universe.parquet")
    oh = _load("index_ohlc.parquet")

    p = adjusted_panel(prices)
    asof = pd.to_datetime(p["date"].max())
    rs = rs_universe(p)

    fno_syms = set()
    if len(fno):
        fno["date"] = pd.to_datetime(fno["date"])
        fno_syms = set(fno[fno["date"] == fno["date"].max()]["symbol"])
    if not fno_syms:                       # fallback: liquid names by turnover
        latest = p[p["date"] == asof]
        fno_syms = set(latest.sort_values("turnover", ascending=False)["symbol"].head(220))

    sub = p[p["symbol"].isin(fno_syms)].sort_values(["symbol", "date"])
    ideas = []
    for sym, g in sub.groupby("symbol", sort=False):
        if pd.to_datetime(g["date"].iloc[-1]) != asof:
            continue                       # stock did not trade today, skip
        f = per_stock(g)
        if f is None:
            continue
        got = classify(sym, f, rs.get(sym, np.nan))
        if got:
            ideas.extend(got)

    longs = sorted([x for x in ideas if x["side"] == "LONG"], key=lambda r: -r["conv"])[:TOP_N]
    shorts = sorted([x for x in ideas if x["side"] == "SHORT"], key=lambda r: -r["conv"])[:TOP_N]
    fades = sorted([x for x in ideas if x["side"].startswith("FADE")], key=lambda r: -r["conv"])[:TOP_N]

    # NEW vs CONT: diff symbols against the prior snapshot
    snapdir = os.path.join(DATA, "opportunities")
    os.makedirs(snapdir, exist_ok=True)
    prior = sorted(glob.glob(os.path.join(snapdir, "*.json")))
    prev_syms = {"LONG": set(), "SHORT": set(), "FADE": set()}
    if prior:
        try:
            pj = json.load(open(prior[-1]))
            for k, arr in (("LONG", pj.get("longs", [])), ("SHORT", pj.get("shorts", [])),
                           ("FADE", pj.get("fades", []))):
                prev_syms[k] = {r["s"] for r in arr}
        except Exception:
            pass
    for arr, k in ((longs, "LONG"), (shorts, "SHORT"), (fades, "FADE")):
        for r in arr:
            r["state"] = "CONT" if r["s"] in prev_syms[k] else "NEW"

    dates = sorted([os.path.basename(x)[:-5] for x in prior] + [asof.strftime("%Y-%m-%d")])
    dates = sorted(set(dates))[-15:]

    payload = dict(asof=asof.strftime("%Y-%m-%d"),
                   universe=len(fno_syms), n=len(ideas),
                   index=index_block(oh), longs=longs, shorts=shorts, fades=fades,
                   dates=dates)

    # dated snapshot (for missed-day catch-up) + latest
    with open(os.path.join(snapdir, f"{payload['asof']}.json"), "w") as fh:
        json.dump(payload, fh, separators=(",", ":"))
    keep = sorted(glob.glob(os.path.join(snapdir, "*.json")))[-KEEP_SNAPSHOTS:]
    for old in set(glob.glob(os.path.join(snapdir, "*.json"))) - set(keep):
        os.remove(old)
    with open(os.path.join(DATA, "opportunities.json"), "w") as fh:
        json.dump(payload, fh, separators=(",", ":"))

    print(f"opportunities {payload['asof']}: {len(longs)} long, {len(shorts)} short, "
          f"{len(fades)} fade  (universe {len(fno_syms)})")
    for r in (longs[:3] + shorts[:3] + fades[:3]):
        print(f"  {r['side']:6s} {r['s']:14s} {r['tag']:4s} {r['conv']} {r['state']}  {r['why']}")


if __name__ == "__main__":
    build()
