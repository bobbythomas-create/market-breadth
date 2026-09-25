"""summary.py: mechanical daily brief for the Today tab.

Reads data/breadth_history.csv plus trader.json, opportunities.json, changes.json
and writes data/summary.json (+ dated copy data/summary/YYYY-MM-DD.json).

Everything here is rule-based and reproducible. No forecasts, no invented numbers.
The Claude analyst note is a separate layer (render.py --note).
Posture labels come from render.regime() so the brief and the dashboard never disagree.
"""
import argparse, json, os, sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from render import regime, ULBL, SECTORS  # single source of truth

CORE = ["ALL", "NIFTY50", "MIDCAP150", "SMALLCAP250", "SEC_BANK", "SEC_IT"]
MORE_CAP = ["LIQUID", "FNO", "NIFTYNEXT50", "NIFTY500"]

# thresholds (same as SKILL.md "Reading it")
BROAD, CORRECTIVE_LO, WASHOUT = 60, 25, 12
BULL_A50, BULL_LARGE_A50, BEAR_A50 = 45, 30, 30
GAP_PTS = 10          # ALL vs NIFTY50 %>50DMA gap worth calling out
ROLL_SHARE = 0.40     # a single day >= 40% of the 5d mover window = roll-off risk
TINY_MOVERS = 5       # fewer 4% movers than this in 5d = ratio not meaningful
MIN_ROLL = 10         # roll-off only called when the departing day had >= this many movers


def f(v, nd=1):
    return None if v is None or (isinstance(v, float) and np.isnan(v)) else round(float(v), nd)


def band(a50):
    if a50 is None:
        return "n/a"
    if a50 < WASHOUT:
        return "washout"
    if a50 < CORRECTIVE_LO:
        return "weak"
    if a50 < 45:
        return "corrective"
    if a50 < BROAD:
        return "neutral"
    return "broad"


def urow(g, u):
    """g: one universe, sorted by date, reset index."""
    last = g.iloc[-1]
    prev5 = g.iloc[-6] if len(g) >= 6 else None
    a50 = f(last["pct_above_50dma"])
    d5 = f(a50 - prev5["pct_above_50dma"]) if prev5 is not None and a50 is not None and not pd.isna(prev5["pct_above_50dma"]) else None
    return {"u": u, "label": ULBL.get(u, u), "n": int(last["universe_count"]),
            "a50": a50, "d5": d5, "a150": f(last.get("pct_above_150dma")),
            "a200": f(last.get("pct_above_200dma")), "posture": regime(last),
            "band": band(a50)}


def rolloff(g):
    """Detect a mechanical 5-day ratio jump caused by a big day leaving the window."""
    if len(g) < 7:
        return None
    t, y, out = g.iloc[-1], g.iloc[-2], g.iloc[-6]  # out = day that just left the 5d window
    r_now, r_prev = t.get("ratio_5d"), y.get("ratio_5d")
    if pd.isna(r_now) or pd.isna(r_prev):
        return None
    win = g.iloc[-6:-1]  # yesterday's 5d window, which still contained `out`
    up_s, dn_s = win["up_4pct"].sum(), win["down_4pct"].sum()
    moved = abs(r_now - r_prev) >= 1.0 or (min(r_now, r_prev) > 0 and max(r_now, r_prev) / min(r_now, r_prev) >= 2)
    if not moved:
        return None
    heavy_dn = dn_s > 0 and out["down_4pct"] / dn_s >= ROLL_SHARE
    heavy_up = up_s > 0 and out["up_4pct"] / up_s >= ROLL_SHARE
    if not (heavy_dn or heavy_up):
        return None
    side, cnt = ("down", int(out["down_4pct"])) if heavy_dn else ("up", int(out["up_4pct"]))
    if cnt < MIN_ROLL:
        return None  # tiny universes: covered by the thin-movers line instead
    return {"from": f(r_prev, 2), "to": f(r_now, 2), "out_date": out["date"].strftime("%d %b"),
            "side": side, "count": cnt, "r10": f(t.get("ratio_10d"), 2)}


def build_summary(data_dir):
    df = pd.read_csv(os.path.join(data_dir, "breadth_history.csv"))
    df["date"] = pd.to_datetime(df["date"])
    asof = df["date"].max()
    G = {u: g.sort_values("date").reset_index(drop=True) for u, g in df.groupby("universe")}
    G = {u: g for u, g in G.items() if g["date"].iloc[-1] == asof}

    def load(name):
        p = os.path.join(data_dir, name)
        try:
            return json.load(open(p)) if os.path.exists(p) else {}
        except Exception:
            return {}
    trader, opps, changes = load("trader.json"), load("opportunities.json"), load("changes.json")

    core = [urow(G[u], u) for u in CORE if u in G]
    more = [urow(G[u], u) for u in MORE_CAP if u in G]
    sect = sorted([urow(G[u], u) for u in SECTORS if u in G], key=lambda r: -(r["a50"] or -1))
    R = {r["u"]: r for r in core + more + sect}

    # ---------------- what the numbers say ----------------
    nums = []
    A = R.get("ALL")
    gA = G.get("ALL")
    if A:
        a5 = f(A["a50"] - A["d5"]) if A["d5"] is not None else None
        traj = "" if a5 is None else f"{a5} to {A['a50']} in 5 sessions ({A['d5']:+.1f}). "
        nums.append(f"ALL %>50 DMA {traj}Band: {A['band']}.")
    L, S = R.get("NIFTY50"), R.get("SMALLCAP250")
    if A and L and A["a50"] is not None and L["a50"] is not None:
        gap = round(A["a50"] - L["a50"], 1)
        tail = f", Smallcap 250 {S['a50']}" if S else ""
        if gap >= GAP_PTS:
            nums.append(f"Large caps are the weak leg: Nifty 50 {L['a50']} vs ALL {A['a50']}{tail} "
                        f"(gap {gap} pts). Index held up by few names; broad participation underneath.")
        elif gap <= -GAP_PTS:
            nums.append(f"Narrow rally: Nifty 50 {L['a50']} vs ALL {A['a50']}{tail} "
                        f"(gap {gap} pts). Large caps carry the index; broad market lags.")
        else:
            nums.append(f"Large vs broad aligned: Nifty 50 {L['a50']} vs ALL {A['a50']} (gap {gap} pts).")
    if sect:
        hi, lo = sect[0], sect[-1]
        wo = [r["label"] for r in sect if r["a50"] is not None and r["a50"] < WASHOUT]
        nums.append(f"Sectors: strongest {hi['label']} {hi['a50']}, weakest {lo['label']} {lo['a50']}"
                    + (f". At washout (<12): {', '.join(wo)}." if wo else "."))
    for u in ("ALL", "NIFTY50"):
        if u in G:
            ro = rolloff(G[u])
            if ro:
                nums.append(f"{ULBL.get(u, u)} 5-day ratio {ro['from']} to {ro['to']} is mechanical: "
                            f"{ro['out_date']} ({ro['count']} stocks {ro['side']} 4%) left the window. "
                            f"Read the 10-day ({ro['r10']}).")
    if "NIFTY50" in G:
        w = G["NIFTY50"].iloc[-5:]
        tot = int(w["up_4pct"].sum() + w["down_4pct"].sum())
        if tot < TINY_MOVERS:
            nums.append(f"Nifty 50 5-day ratio ({f(G['NIFTY50'].iloc[-1]['ratio_5d'], 2)}) rests on only {tot} "
                        f"stocks moving 4% in 5 sessions: not meaningful.")

    # signals
    firing, recent = [], []
    for u in CORE + MORE_CAP:
        if u not in G:
            continue
        g = G[u]
        t = g.iloc[-1]
        lab = ULBL.get(u, u)
        for k, nm in (("thrust", "thrust"), ("div_bearish", "bear divergence"), ("div_bullish", "bull divergence")):
            if bool(t.get(k, False)):
                firing.append(f"{nm} in {lab}")
        # base rates are measured on cap universes only; sector washouts are reported in the sector line
        if not pd.isna(t["pct_above_50dma"]) and t["pct_above_50dma"] < WASHOUT and not u.startswith("SEC_"):
            firing.append(f"washout in {lab}")
        prior = g.iloc[-6:-1]
        if bool(prior["thrust"].fillna(False).astype(bool).any()) and not bool(t.get("thrust", False)):
            d = prior[prior["thrust"].fillna(False).astype(bool)]["date"].iloc[-1].strftime("%d %b")
            recent.append(f"{lab} ({d})")
    all_thr10 = bool(gA is not None and gA.iloc[-10:]["thrust"].fillna(False).astype(bool).any())
    sig_txt = ("Firing: " + ", ".join(firing) + ".") if firing else "No base-rate signal firing (washout, thrust, divergence)."
    if recent:
        sig_txt += f" Recent thrust: {', '.join(recent)}" + ("" if all_thr10 else ", not confirmed in ALL, so not a durable-low signature") + "."
    nums.append(sig_txt)
    if gA is not None and len(gA) >= 6:
        t, p = gA.iloc[-1], gA.iloc[-6]
        nh, nl, pnh, pnl = int(t["new_52w_high"]), int(t["new_52w_low"]), int(p["new_52w_high"]), int(p["new_52w_low"])
        cross = ""
        if (nh > nl) != (pnh > pnl):
            cross = " Crossed: highs now lead." if nh > nl else " Crossed: lows now lead."
        nums.append(f"52-week highs vs lows (ALL): {nh} vs {nl}, 5 sessions ago {pnh} vs {pnl}.{cross}")

    # ---------------- F&O posture ----------------
    idx = [{"label": x.get("label"), "trend": x.get("trend"), "posture": x.get("posture"), "ret1": x.get("ret1")}
           for x in (opps.get("index") or [])]
    vix = (trader.get("index") or {}).get("vix") or {}
    ivr = vix.get("ivrank")
    if ivr is None:
        hint = ""
    elif ivr <= 25:
        hint = "Options cheap: defined-risk debit spreads over premium selling."
    elif ivr >= 60:
        hint = "Options rich: defined-risk credit spreads over buying premium."
    else:
        hint = "Options fairly priced: pick structure by view, keep risk defined."
    def top(side, k=6):
        return [{"s": r.get("s"), "conv": r.get("tag"), "state": r.get("state")} for r in (opps.get(side) or [])[:k]]
    squeeze = [c.get("msg") for c in (changes.get("changes") or []) if "SQUEEZE" in str(c.get("tag", "")).upper()]
    fno = {"index": idx,
           "vol": {"vix": vix.get("level"), "ivrank": ivr, "vrp_pctile": vix.get("vrp_pctile"),
                   "exp_1w_pct": vix.get("exp_move_1w_pct"), "exp_1w_pts": vix.get("exp_move_1w_pts"), "hint": hint},
           "longs": top("longs"), "shorts": top("shorts"), "fades": top("fades"),
           "n_longs": len(opps.get("longs") or []), "n_shorts": len(opps.get("shorts") or []),
           "n_fades": len(opps.get("fades") or []), "squeeze": squeeze,
           "opps_asof": opps.get("asof")}

    # ---------------- what flips the read ----------------
    bull, bear = [], []
    if A and A["a50"] is not None:
        need = round(BULL_A50 - A["a50"], 1)
        bull.append(f"ALL %>50 DMA above {BULL_A50} (now {A['a50']}" + (f", needs +{need})" if need > 0 else ", met)"))
        cush = round(A["a50"] - BEAR_A50, 1)
        bear.append(f"ALL back under {BEAR_A50} (now {A['a50']}" + (f", {cush} pts cushion)" if cush > 0 else ", met)"))
    if gA is not None:
        t = gA.iloc[-1]
        n = max(int(t["universe_count"]), 1)
        up, dn = int(t["up_4pct"]), int(t["down_4pct"])
        rt = f"{up / dn:.1f}:1" if dn else "n/a"
        bull.append(f"Thrust day in ALL: >10% of universe up 4% at 3:1 (today {100 * up / n:.1f}%, {rt})")
        bear.append(f"New lows retake new highs (now {int(t['new_52w_high'])} vs {int(t['new_52w_low'])})")
    if L and L["a50"] is not None:
        bull.append(f"Nifty 50 %>50 DMA reclaims {BULL_LARGE_A50} (now {L['a50']})")
    bear.append(f"Washout (<{WASHOUT}) then in play: bearish extremes carry the better base rate for bottoms")

    # ---------------- headline + bottom line ----------------
    head, bottom = "n/a", ""
    if A:
        d5 = A["d5"] or 0
        traj = "recovering" if d5 >= 3 else "deteriorating" if d5 <= -3 else "flat"
        conf = "confirmed by a thrust" if all_thr10 else "not confirmed"
        head = f"{A['band']}, {traj}, {conf}"
        parts = [f"{A['band'].capitalize()} regime (ALL {A['a50']}, {d5:+.1f} in 5d)."]
        if L and L["a50"] is not None and A["a50"] - L["a50"] >= GAP_PTS:
            parts.append("Broad participation healing faster than large caps.")
        elif L and L["a50"] is not None and A["a50"] - L["a50"] <= -GAP_PTS:
            parts.append("Large caps carrying a narrow tape.")
        lean = (idx[0]["trend"] if idx else None) or "n/a"
        if firing:
            parts.append(f"Signal firing ({', '.join(firing)}): check its base rate on the panel before acting.")
        else:
            parts.append(f"No base-rate signal: index lean follows the trend ({lean}) until one fires.")
        if A["band"] in ("corrective", "weak", "washout"):
            parts.append("Stock-specific Stage-2 longs and Stage-4 shorts at small size.")
        elif A["band"] == "broad":
            parts.append("Press Stage-2 leaders; normal size.")
        if hint:
            parts.append(hint)
        bottom = " ".join(parts)

    return {"asof": asof.strftime("%Y-%m-%d"),
            "prev_asof": (gA.iloc[-2]["date"].strftime("%Y-%m-%d") if gA is not None and len(gA) > 1 else None),
            "headline": head, "core": core, "more": more, "sectors": sect,
            "numbers": nums, "fno": fno, "flips": {"bull": bull, "bear": bear}, "bottom": bottom}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="data")
    a = ap.parse_args()
    s = build_summary(a.data)
    out = os.path.join(a.data, "summary.json")
    json.dump(s, open(out, "w"), indent=1)
    os.makedirs(os.path.join(a.data, "summary"), exist_ok=True)
    json.dump(s, open(os.path.join(a.data, "summary", s["asof"] + ".json"), "w"), indent=1)
    print(f"summary -> {out} asof {s['asof']} | {s['headline']}")


if __name__ == "__main__":
    main()
