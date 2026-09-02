#!/usr/bin/env python3
"""
Build data/frameworks.json: a map of NSE symbol -> list of Playbook frameworks
it appears in, for the watchlist bridge.

Source CSVs are the user's Screener framework exports. Place them in a
'frameworks/' folder in the repo (or pass --src). Each CSV has 'NSE Code' in a
column; we read that column by header.

A stock appearing in the Screen (Stage-2) AND one or more frameworks is the
user's multi-screen consensus rule made automatic.

Usage
  python frameworks.py --src frameworks
"""

import argparse, csv, datetime, glob, json, os

DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data")

# filename fragment -> short label
LABELS = {
    "coffeecan": "Coffee Can", "consistentcompounder": "Consistent Compounder",
    "garp": "GARP", "cashisking": "Cash-is-King", "peterlynch": "Peter Lynch",
    "vijaymalik": "Vijay Malik", "piotroski": "Piotroski", "100bagger": "100-Bagger",
    "superstar": "Superstar", "bigbull": "Superstar", "marquee": "Superstar",
}


def label_for(fname):
    low = fname.lower()
    for frag, lab in LABELS.items():
        if frag in low:
            return lab
    return os.path.splitext(os.path.basename(fname))[0][:18]


def build(src):
    m = {}
    files = sorted(glob.glob(os.path.join(src, "*.csv")))
    if not files:
        print(f"no CSVs in {src}, writing empty frameworks.json")
        json.dump({"frameworks": [], "map": {}, "built": datetime.date.today().isoformat(),
               "superstar_present": False}, open(os.path.join(DATA, "frameworks.json"), "w"))
        return
    fw_list = []
    for f in files:
        lab = label_for(f)
        fw_list.append(lab)
        try:
            with open(f, encoding="utf-8", errors="replace") as fh:
                rdr = csv.reader(fh)
                hdr = next(rdr)
                idx = next((i for i, h in enumerate(hdr) if "NSE" in h and "Code" in h), None)
                if idx is None:
                    continue
                for row in rdr:
                    if len(row) > idx:
                        sym = row[idx].strip().upper()
                        if sym and sym != "NSE CODE":
                            m.setdefault(sym, [])
                            if lab not in m[sym]:
                                m[sym].append(lab)
        except Exception as e:
            print(f"  {os.path.basename(f)}: {e}")
    os.makedirs(DATA, exist_ok=True)
    json.dump({"frameworks": sorted(set(fw_list)), "map": m,
               "built": datetime.date.today().isoformat(),
               "superstar_present": any("superstar" in f.lower() for f in files)},
              open(os.path.join(DATA, "frameworks.json"), "w"), separators=(",", ":"))
    multi = sum(1 for v in m.values() if len(v) >= 2)
    print(f"frameworks.json: {len(m)} symbols across {len(set(fw_list))} frameworks, {multi} in 2+")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", default="frameworks")
    a = ap.parse_args()
    build(a.src)
