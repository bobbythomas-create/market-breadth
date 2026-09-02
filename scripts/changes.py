#!/usr/bin/env python3
"""
State-change detector, run LAST in the pipeline (after breadth, screen, trader).

Compares today's state against a persisted snapshot and writes data/changes.json:
a short list of only the things that MATERIALLY changed since last session, across
both books (investing regime + trading vol/squeeze).

The point: most days nothing changes. This lets the user act only at the turns.

Reads : data/breadth_history.csv, data/trader.json, data/stocks.json, data/state_snapshot.json
Writes: data/changes.json, data/state_snapshot.json (updated)

Usage
  python changes.py
"""

import json, os
import pandas as pd

DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data")


def load_json(name, default=None):
    p = os.path.join(DATA, name)
    if os.path.exists(p):
        try:
            return json.load(open(p))
        except Exception:
            pass
    return default if default is not None else {}


def regime_of(a50):
    if a50 is None:
        return "n/a"
    if a50 >= 60:
        return "Aggressive"
    if a50 >= 45:
        return "Normal"
    if a50 >= 25:
        return "Defensive"
    return "Stand aside"


def build():
    # --- gather today's state ---
    b = pd.read_csv(os.path.join(DATA, "breadth_history.csv"), parse_dates=["date"])
    liq = b[b.universe == "LIQUID"].sort_values("date")
    if not len(liq):
        liq = b[b.universe == "ALL"].sort_values("date")
    last = liq.iloc[-1]
    asof = last["date"].strftime("%Y-%m-%d")

    trader = load_json("trader.json")
    stocks = load_json("stocks.json")
    vix = trader.get("index", {}).get("vix", {})
    idxs = {i["tag"]: i for i in trader.get("index", {}).get("indices", [])}
    fno = trader.get("fno", {})

    now = {
        "asof": asof,
        "regime": regime_of(float(last.get("pct_above_50dma"))),
        "a50": round(float(last.get("pct_above_50dma")), 1),
        "a10": round(float(last.get("pct_above_10dma")), 1),
        "r5": round(float(last.get("ratio_5d")), 2) if not pd.isna(last.get("ratio_5d")) else None,
        "vix": vix.get("level"),
        "ivrank": vix.get("ivrank"),
        "nifty_state": idxs.get("NIFTY50", {}).get("state"),
        "nifty_atr_pctile": idxs.get("NIFTY50", {}).get("atr_pctile"),
        "bank_state": idxs.get("BANKNIFTY", {}).get("state"),
        "strict": stocks.get("n_strict"),
        "strict_names": sorted([x["s"] for x in stocks.get("stocks", []) if x.get("strict")]),
    }

    prev = load_json("state_snapshot.json")
    changes = []

    def flag(tag, msg, book):
        changes.append({"tag": tag, "msg": msg, "book": book})

    if prev:
        # regime flip
        if prev.get("regime") != now["regime"]:
            flag("REGIME", f"Regime {prev.get('regime')} -> {now['regime']}", "invest")
        # 10 DMA fast move (swing-relevant)
        if prev.get("a10") is not None and now["a10"] is not None:
            d = now["a10"] - prev["a10"]
            if abs(d) >= 8:
                flag("SHORT-TERM", f"10 DMA breadth {'+' if d>0 else ''}{d:.0f} pts ({prev['a10']:.0f}->{now['a10']:.0f})",
                     "both")
        # 5-day ratio crossing an extreme
        for lvl, lbl in [(5.0, "aggressive extreme"), (0.5, "defensive extreme")]:
            pv, nv = prev.get("r5"), now["r5"]
            if pv is not None and nv is not None:
                crossed_up = pv < lvl <= nv and lvl == 5.0
                crossed_dn = pv > lvl >= nv and lvl == 0.5
                if crossed_up or crossed_dn:
                    flag("RATIO", f"5-day ratio hit {lbl} ({nv})", "both")
        # VIX regime shift (IV rank buckets)
        def ivbucket(r):
            return "cheap" if (r is not None and r <= 30) else "rich" if (r is not None and r >= 70) else "mid"
        if ivbucket(prev.get("ivrank")) != ivbucket(now["ivrank"]):
            flag("VIX", f"Options vol {ivbucket(prev.get('ivrank'))} -> {ivbucket(now['ivrank'])} "
                        f"(IV Rank {now['ivrank']})", "trade")
        # Nifty squeeze state change
        if prev.get("nifty_state") != now["nifty_state"] and now["nifty_state"]:
            flag("NIFTY VOL", f"Nifty {prev.get('nifty_state')} -> {now['nifty_state']}", "trade")
        if prev.get("bank_state") != now["bank_state"] and now["bank_state"]:
            flag("BANK VOL", f"Bank Nifty {prev.get('bank_state')} -> {now['bank_state']}", "trade")
        # new strict names entering the screen
        prev_names = set(prev.get("strict_names", []))
        new_names = [s for s in now["strict_names"] if s not in prev_names] if prev_names else []
        if new_names:
            flag("SCREEN", f"{len(new_names)} new Stage-2 names: {', '.join(new_names[:6])}"
                           + ("..." if len(new_names) > 6 else ""), "invest")

    # squeeze fires (from trader.json, always surfaced)
    fired = fno.get("fired", [])
    if fired:
        names = ", ".join(f"{f['s']} ({f['lean']})" for f in fired[:6])
        flag("SQUEEZE FIRE", f"{len(fired)} F&O names broke their coil: {names}", "trade")
    # events
    ev = fno.get("events", [])
    if ev:
        names = ", ".join(f"{e['s']} {'+' if e['chg']>0 else ''}{e['chg']}%" for e in ev[:6])
        flag("EVENT", f"{len(ev)} F&O names moved >|8%|: {names}", "trade")

    out = {"asof": asof, "changes": changes,
           "quiet": len(changes) == 0,
           "prev_asof": prev.get("asof") if prev else None}
    json.dump(out, open(os.path.join(DATA, "changes.json"), "w"), separators=(",", ":"))
    json.dump(now, open(os.path.join(DATA, "state_snapshot.json"), "w"))

    print(f"changes -> data/changes.json  ({len(changes)} change(s) since {out['prev_asof']})")
    for c in changes:
        print(f"  [{c['book']}] {c['tag']}: {c['msg']}")
    if not changes:
        print("  quiet session, nothing material changed")


if __name__ == "__main__":
    build()
