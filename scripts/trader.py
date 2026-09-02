#!/usr/bin/env python3
"""
Trader view computation, run AFTER ingest.py.

Reads : data/prices.parquet (OHLC), data/index_ohlc.parquet (Nifty/BankNifty/VIX),
        data/fno_universe.parquet
Writes: data/trader.json

Everything here is a CONTEXT gauge, not a standalone buy/sell signal. The variance
risk premium in particular is documented to lack robustness as a direct trigger.

Signals
  - India VIX: IV Rank and IV Percentile (252-day), the industry cheap/dear standard
  - VIX vs realised Nifty vol at 10/20/30 days; 30d is the VIX-matched window
  - Expected 1-SD move for the week (VIX / sqrt(252) * sqrt(5))
  - VIX-Nifty divergence (rally the options market distrusts)
  - Index squeeze/expansion: ATR percentile + range-vs-recent for Nifty, Bank Nifty
  - Per-F&O-stock squeeze rank (ATR compression) — needs OHLC history
  - Today's +/-8% event movers in the F&O universe

Usage
  python trader.py
"""

import json, os
import numpy as np
import pandas as pd

DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data")
TRADING_DAYS = 252


def _load(name):
    p = os.path.join(DATA, name)
    return pd.read_parquet(p) if os.path.exists(p) else None


def realised_vol(close, window):
    """Annualised close-to-close realised vol over `window` sessions, in %."""
    r = np.log(close / close.shift(1))
    return r.rolling(window).std() * np.sqrt(TRADING_DAYS) * 100


def atr(df, window=14):
    """Average True Range as % of close. df has high, low, close."""
    h, l, c = df["high"], df["low"], df["close"]
    pc = c.shift(1)
    tr = pd.concat([h - l, (h - pc).abs(), (l - pc).abs()], axis=1).max(axis=1)
    return (tr.rolling(window).mean() / c) * 100


def pctile(series, value):
    s = series.dropna()
    if len(s) < 20 or pd.isna(value):
        return None
    return round(float((s <= value).mean() * 100), 0)


def iv_rank(series):
    """IV Rank = (current - min) / (max - min) over the window."""
    s = series.dropna()
    if len(s) < 20:
        return None
    cur, lo, hi = s.iloc[-1], s.min(), s.max()
    if hi == lo:
        return None
    return round(float((cur - lo) / (hi - lo) * 100), 0)


def index_block(oh):
    """VIX + Nifty + Bank Nifty vol state."""
    out = {}
    if oh is None or not len(oh):
        return out
    oh = oh.copy(); oh["date"] = pd.to_datetime(oh["date"])
    piv = {tag: g.sort_values("date").set_index("date")
           for tag, g in oh.groupby("index")}

    # VIX
    if "INDIAVIX" in piv:
        v = piv["INDIAVIX"]["close"].tail(TRADING_DAYS + 5)
        cur = float(v.iloc[-1]) if len(v) else None
        out["vix"] = {
            "level": round(cur, 2) if cur is not None else None,
            "ivrank": iv_rank(v.tail(TRADING_DAYS)),
            "ivpctile": pctile(v.tail(TRADING_DAYS), cur),
        }
        # expected weekly move needs Nifty level
        if "NIFTY50" in piv and cur is not None:
            nlvl = float(piv["NIFTY50"]["close"].iloc[-1])
            daily = cur / np.sqrt(TRADING_DAYS)
            out["vix"]["exp_move_1d_pct"] = round(daily, 2)
            out["vix"]["exp_move_1w_pct"] = round(daily * np.sqrt(5), 2)
            out["vix"]["exp_move_1w_pts"] = round(nlvl * daily / 100 * np.sqrt(5), 0)

        # VIX vs realised Nifty vol (10/20/30d). 30d is VIX-matched.
        if "NIFTY50" in piv:
            nc = piv["NIFTY50"]["close"]
            hv = {w: realised_vol(nc, w).iloc[-1] for w in (10, 20, 30)}
            out["vix"]["hv"] = {str(w): (None if pd.isna(x) else round(float(x), 1)) for w, x in hv.items()}
            if cur is not None and not pd.isna(hv[30]):
                spread = cur - hv[30]
                out["vix"]["vrp_30d"] = round(float(spread), 1)
                # spread vs its own history
                hv30_series = realised_vol(nc, 30)
                vser = piv["INDIAVIX"]["close"]
                al = pd.concat([vser, hv30_series], axis=1).dropna()
                if len(al) > 40:
                    sp = al.iloc[:, 0] - al.iloc[:, 1]
                    out["vix"]["vrp_pctile"] = pctile(sp.tail(TRADING_DAYS), spread)
        # VIX-Nifty divergence: both up over 5 sessions
        if "NIFTY50" in piv:
            vch = v.iloc[-1] - v.iloc[-6] if len(v) > 6 else np.nan
            nch = piv["NIFTY50"]["close"].iloc[-1] / piv["NIFTY50"]["close"].iloc[-6] - 1 if len(piv["NIFTY50"]) > 6 else np.nan
            if not pd.isna(vch) and not pd.isna(nch):
                out["vix"]["divergence"] = bool(vch > 0 and nch > 0)

    # index squeeze/expansion via ATR percentile
    out["indices"] = []
    for tag, label in [("NIFTY50", "Nifty 50"), ("BANKNIFTY", "Bank Nifty")]:
        if tag not in piv:
            continue
        g = piv[tag]
        if not {"high", "low", "close"}.issubset(g.columns) or g["high"].isna().all():
            out["indices"].append({"tag": tag, "label": label, "state": "no OHLC yet"})
            continue
        a = atr(g, 14)
        cur = a.iloc[-1]
        pr = pctile(a.tail(TRADING_DAYS), cur)
        state = "squeeze" if (pr is not None and pr <= 20) else "expansion" if (pr is not None and pr >= 80) else "normal"
        out["indices"].append({
            "tag": tag, "label": label, "atr_pct": None if pd.isna(cur) else round(float(cur), 2),
            "atr_pctile": pr, "state": state,
        })
    return out


def fno_block(prices, fno):
    """Per-F&O-stock squeeze rank + today's +/-8% event movers."""
    out = {"squeeze": [], "events": [], "fired": [], "has_ohlc": False}
    if prices is None or fno is None:
        return out
    prices = prices.copy(); prices["date"] = pd.to_datetime(prices["date"])
    fno = fno.copy(); fno["date"] = pd.to_datetime(fno["date"])
    asof = prices["date"].max()
    members = set(fno[fno["date"] == fno["date"].max()]["symbol"].astype(str))
    p = prices[prices["symbol"].isin(members)]
    has_ohlc = "high" in p.columns and not p["high"].isna().all()
    out["has_ohlc"] = bool(has_ohlc)
    out["asof"] = asof.strftime("%Y-%m-%d")

    # event movers today: close/prev_close beyond +/-8%
    today = p[p["date"] == asof].copy()
    today["chg"] = (today["close"] / today["prev_close"] - 1) * 100
    ev = today[today["chg"].abs() >= 8].sort_values("chg", ascending=False)
    out["events"] = [{"s": r["symbol"], "chg": round(float(r["chg"]), 1),
                      "px": round(float(r["close"]), 1)} for _, r in ev.iterrows()]

    if not has_ohlc:
        return out    # squeeze needs OHLC history; wait for backfill

    # per-stock ATR percentile; low percentile = coiled. Also detect squeeze-FIRES:
    # a name that was coiled a few sessions ago and whose ATR has now jumped (expansion started).
    recs, fired = [], []
    for sym, g in p.sort_values("date").groupby("symbol"):
        if len(g) < 60:
            continue
        a = atr(g, 14)
        cur = a.iloc[-1]
        if pd.isna(cur):
            continue
        aser = a.tail(TRADING_DAYS)
        pr = pctile(aser, cur)
        if pr is None:
            continue
        c = g.iloc[-1]
        chg = float(c["close"] / c["prev_close"] - 1) * 100
        # trend lean: price vs its 50-session mean
        m50 = g["close"].tail(50).mean()
        lean = "up" if c["close"] > m50 else "dn"
        rec = {"s": sym, "atr_pctile": pr, "atr_pct": round(float(cur), 2),
               "px": round(float(c["close"]), 1), "chg": round(chg, 1), "lean": lean}
        recs.append(rec)
        # fire: percentile 5 sessions ago was <=15, now >=35, i.e. it broke out of the coil
        if len(a) > 6:
            pr_prev = pctile(a.tail(TRADING_DAYS + 5).head(-5) if len(a) > TRADING_DAYS + 5 else a.iloc[:-5], a.iloc[-6])
            if pr_prev is not None and pr_prev <= 15 and pr >= 35:
                fired.append({**rec, "was": pr_prev})
    recs.sort(key=lambda r: r["atr_pctile"])
    fired.sort(key=lambda r: -abs(r["chg"]))
    out["squeeze"] = recs[:40]
    out["fired"] = fired[:20]
    return out


def build():
    oh = _load("index_ohlc.parquet")
    prices = _load("prices.parquet")
    fno = _load("fno_universe.parquet")

    asof = "?"
    if prices is not None:
        asof = pd.to_datetime(prices["date"]).max().strftime("%Y-%m-%d")

    out = {"asof": asof, "index": index_block(oh), "fno": fno_block(prices, fno)}
    with open(os.path.join(DATA, "trader.json"), "w") as f:
        json.dump(out, f, separators=(",", ":"))

    ib = out["index"]
    vix = ib.get("vix", {})
    print(f"trader -> data/trader.json  asof {asof}")
    if vix:
        print(f"  VIX {vix.get('level')} IVrank {vix.get('ivrank')} IVpctile {vix.get('ivpctile')} "
              f"VRP30 {vix.get('vrp_30d')} (pctile {vix.get('vrp_pctile')})")
    for ix in ib.get("indices", []):
        print(f"  {ix['label']}: {ix.get('state')} (ATR pctile {ix.get('atr_pctile')})")
    fb = out["fno"]
    print(f"  F&O events (>|8%|): {len(fb['events'])}  squeeze names: {len(fb['squeeze'])}  "
          f"(OHLC history: {fb['has_ohlc']})")


if __name__ == "__main__":
    build()
