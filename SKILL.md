---
name: market-breadth
description: Render and interpret an Indian market-breadth and trading-opportunity dashboard (Stockbee-style market monitor plus an F&O opportunity engine) from NSE end-of-day data. It carries 20 universes (8 market-cap plus 12 sector), a top action panel with F&O-first posture and 2019-based signal base rates, and an Opportunities tab of ranked long / short / fade F&O ideas exportable to TradingView. ALWAYS use this skill when the user types /market-breadth, asks for market breadth, a breadth dashboard, the market monitor, advance-decline, 4% movers, percentage of stocks above their 10/20/30wk/50/200 DMA, new 52-week highs vs lows, whether the market is broadening or narrowing, whether a Nifty move is confirmed by participation, what to trade in Nifty / Bank Nifty / stock F&O today, or asks to update, refresh, backfill or extend their breadth, index or opportunity history.
---

# Market Breadth + Opportunity Dashboard

## Daily routine (tell the user this)

There is nothing to run. A GitHub Actions job (`breadth-daily.yml`) is scheduled for 19:15 IST Mon to Fri, but GitHub's free scheduler delays it, so runs actually land and commit around **22:45 to 23:16 IST**. The user's only daily step is to type `/market-breadth` any time after **23:30 IST**. Claude then refreshes, writes the Claude note, renders, and **republishes the same private page**. Only touch the Actions tab if a trading day passes with no new commit by the next morning, or if this skill reports stale data.

**Published dashboard (update this one, never create a second):** `https://claude.ai/artifact/Fkr8LNiS3uJ6KpAe7YA4RN`

## Decide the mode first

| Situation | Mode |
|---|---|
| Repo exists and the daily job runs (the normal case) | **Refresh and render** |
| User uploads a CSV or xlsx | **Render from upload** |
| No data anywhere yet, or a fresh repo | **Bootstrap** (see `references/setup.md`) |
| User asks a question and data exists | **Read** (render only if they want the page) |

## Non-negotiable token rules

Breadth history is thousands of numbers; almost none belong in context.

1. **Never** print, cat, or read `breadth_history.csv` into context. Scripts read it, not you.
2. `render.py` prints a one-line-per-universe summary. That summary plus the panel content is all you need for commentary.
3. If you must inspect data, use pandas in a script and print only aggregates or `.tail(5)`. Print only `headline`, `numbers` and `bottom` from `summary.json`, not the whole file.
4. On a daily refresh only new rows are appended. Do not regenerate history unless asked.

Budget: a refresh, note, render and publish should cost well under 3,000 tokens of context.

## Mode: refresh and render (the default)

The daily job already wrote the data. Five steps, in order.

**1. Pull** the CSV, every JSON side-file (they feed the Trader, Opportunities, Screen, Frameworks and Changes tabs, the top panel and the Daily brief), `render.py` and `summary.py`, plus the last 10 sessions of stock lists and opportunity snapshots (the published page cannot fetch from GitHub, so they are embedded inline). Missing side-files render as empty tabs, so never skip them.

```bash
mkdir -p ~/mb/data/lists ~/mb/data/opportunities ~/mb/scripts && cd ~/mb
B="https://raw.githubusercontent.com/bobbythomas-create/market-breadth/main"
for f in breadth_history.csv validation.txt stocks.json trader.json changes.json frameworks.json opportunities.json summary.json; do
  curl -sL "$B/data/$f" -o "data/$f"
done
for f in render.py summary.py; do curl -sL "$B/scripts/$f" -o "scripts/$f"; done
for d in $(python3 -c "import pandas as pd;print(' '.join(sorted(pd.read_csv('data/breadth_history.csv',usecols=['date']).date.unique())[-10:]))"); do
  curl -sfL "$B/data/lists/$d.json" -o "data/lists/$d.json" || rm -f "data/lists/$d.json"
  curl -sfL "$B/data/opportunities/$d.json" -o "data/opportunities/$d.json" || rm -f "data/opportunities/$d.json"
done
cat data/validation.txt | head -12
python3 -c "import json;s=json.load(open('data/summary.json'));print(s['asof'],s['headline']);print(*s['numbers'],sep='\n');print(s['bottom'])"
```

Do not use the GitHub API tree endpoint for this; it rate-limits the sandbox. Derive dates from the CSV as above.

**2. Check** freshness (below) and validation. If `summary.json` is missing or its `asof` is behind the CSV, `render.py` recomputes it in-process; say so.

**3. Write the Claude note** to `/tmp/note.json` as `{"asof": "<CSV latest date>", "points": ["...", "..."]}`. 2 to 5 points, keyword-style, judgement only (see "The Claude note" below). Pull any extra numbers you need with a pandas one-liner that prints aggregates, never the CSV.

**4. Render** with the note:

```bash
python3 scripts/render.py --csv data/breadth_history.csv --out /mnt/user-data/outputs/breadth_dashboard.html --rows 250 --note /tmp/note.json
```

**5. Publish** with the Artifact tool, action `publish`, `file_path` `/mnt/user-data/outputs/breadth_dashboard.html`, `url` = the published dashboard URL above (so the same page updates), title `Market Breadth`, favicon 📊. Do not also call present_files. Then give the chat reply (terse, per "Reading it"), which should not repeat the brief line by line: lead with the headline, the 2 to 3 things that matter, and the posture.

Older snapshots beyond 10 sessions: `data/opportunities/YYYY-MM-DD.json` and `data/lists/YYYY-MM-DD.json` (90 sessions) stay in the repo. Fetch only the one day asked for, never the folder.

`raw.githubusercontent.com` is reachable from the sandbox. `nseindia.com` is not, so never fetch prices or index data directly here; that runs in Actions.

**Freshness check before commenting.** Compare the CSV's latest date with the last completed NSE session (today if after the evening job lands, otherwise the prior weekday; check holidays).  If they differ, say the file is stale and by how many sessions, write the note's `asof` as the CSV date (never today's date), and do not carry the last row forward.

## The Claude note

The mechanical brief (`summary.py`) already states the table, the numbers, the F&O posture, the flip triggers and the bottom line. The note exists for what rules cannot see. Good note points:

- Corrections: where the mechanical read or a previous chat read was wrong or incomplete. Own the error plainly.
- Cross-checks the rules skip: sector 150 vs 200 DMA structure (base vs Stage 4), thrusts in several segments but not ALL, rotation character (defensive leaders vs risk-on).
- Context that changes the posture: an event (RBI, Fed, results, expiry) inside the expected-move window, a validation finding, stale data.

Rules: never restate a number the brief already shows unless correcting it; never invent a level, price or catalyst; label inference as inference; no forecasts of specific index levels; frame as research. If nothing adds value, write one point saying the mechanical read stands. No em-dashes.

## Mode: render from upload

Uploads land in `/mnt/user-data/uploads`. If the file matches the schema in `references/metric_definitions.md`, render it directly. If it is the user's Chartink or Trade Sensei workbook, map the columns you can, leave the rest null, and state which metrics are missing rather than approximating them.

## Mode: bootstrap

Two moving parts in different places. **Ingest** runs outside the chat (`ingest.py` downloads NSE UDiFF bhavcopy CM+FO and `ind_close_all` index OHLC, maintains an adjusted price store, and appends one row per session per universe). **Render** runs here. Walk the user through `references/setup.md`. First run:

```bash
pip install pandas pyarrow requests
python3 scripts/ingest.py --backfill 2024-04-01   # 200 DMA needs ~1yr runway before the first usable row
python3 scripts/ingest.py --recompute             # rebuild breadth from the store
python3 scripts/render.py --rows 250
```

Deep history to 2019 was a one-time Kite backfill (now retired; credentials rotated). Do not attempt a Kite pull. To rebuild breadth under a changed metric, use the recompute button, not a re-fetch.

## What the dashboard shows now

- **Daily brief (top of the Today tab).** From `summary.json` plus the optional Claude note: a regime table (6 core universes with %>50 DMA, 5-day change, %>150, %>200 DMA, posture; a collapsible section with the other 4 cap segments and all 12 sectors), "What the numbers say" bullets, F&O posture, "What flips the read", and a Bottom line. Every cell comes from the CSV; nothing is typed by hand.
- **Top action panel (server-rendered, on every tab).** An F&O-first action list (Nifty F&O, Bank Nifty F&O, stock F&O, and Nifty IT as context once backfilled, then rest) with a mechanical posture; then the breadth regime, the signal table with **2019-based forward-return base rates** (washout, washout+thrust, thrust, bear/bull divergence, each with its current firing state), a sector rotation strip, a one-line posture, terse observations, and a **Bottom line** synthesis (regime, the day-over-day breadth delta, and the high-conviction watchlist or the firing edge, framed as not an entry until a base-rate signal fires). The ten-second read.
- **Opportunities tab.** Ranked long / short / fade ideas across the single-stock F&O universe, each with a conviction tag (High/Med/Low = a ranking, NOT a win-rate), a NEW/CONT flag, and a why line, plus an index block and TradingView copy buttons and a session selector for prior days. Fades are mean-reversion-after-expansion, counter-trend, capped lower.
- **20 universes.** 8 cap (ALL, LIQUID, FNO, NIFTY50, NIFTYNEXT50, MIDCAP150, SMALLCAP250, NIFTY500) plus 12 sector (SEC_*). Bank Nifty (SEC_BANK) and Nifty IT (SEC_IT) are also in the top universe selector.
- **Primary tabs:** Today, Opportunities, Trader, Charts. Under "More": Screen, Table, Sectors, Segments, Regime, Scanner, Guide, Reference. Sectors and Segments have per-group "copy F&O to TradingView" chips. The Charts tab has a 1M/3M/6M/1Y/2Y/All period selector and 30/45/60 band labels, plus a **Breadth trend** chart: multi-select across the 0-100 DMA family (%>10/20/40/50/150/200 DMA), a daily/weekly/monthly resolution toggle, full 2019-present history, value labels, a neutral-50 line, and an auto 3-point commentary, turning any universe's breadth into a trend rather than a table of numbers. The Reference tab carries the metric definitions, the India calibration, curated learning videos, and a Next-phase build backlog.

## Reading it

Keep it terse: bullets or a short list, not prose. Anchor on:

- **% above 50 DMA** is the primary regime gauge. Above 60 broad, 25 to 45 corrective, below 12 a washout.
- **% above 150 DMA** is the 30-week Weinstein stage line: rising with price above = Stage 2, falling with price below = Stage 4. It sits between the 50 and 200 DMA reads.
- **5-day / 10-day ratios**, India-calibrated. Indian 5-day median near 1.7 (US near 1.0) because the EQ universe carries thin microcaps. Above 5.0 aggressive extreme, below 0.5 defensive extreme. Bonde's US 2.0/0.5 do not transfer.
- **Breadth is most useful at extremes** and is asymmetric: bearish extremes call bottoms well, bullish extremes call tops poorly. Do not over-read mid-range readings.
- **Net 4% movers**: a day above 10% of the universe up with a 3:1 ratio is a thrust, the signature of a durable low.
- **Base rates** are the real probabilities and only count when a signal fires. Conviction tags on stocks are rankings, not win-rates. Say this plainly; never attach fake percentages.
- **ALL vs NIFTY50/SMALLCAP gap** is usually the most tradeable observation.

Say what the numbers show, what would flip the read, and the mechanical posture. Do not predict specific levels. This is the user's private research tool, so a posture is fine; frame it as research, not advice.

## Workflows

- **`breadth-daily.yml`** (scheduled): ingest, then frameworks, screen, trader, changes, opportunities, summary, render, commit. The nightly `dashboard.html` in the repo carries the mechanical brief only; the Claude note exists only on the published page.
- **`recompute-breadth.yml`** (manual button): rebuild the ENTIRE breadth history from the price store after any `ingest.py` change (e.g. the 200 DMA fix, the 30-week line). Re-runnable, ~30-60 min.
- **`index-backfill.yml`** (manual button, input = start date): light index-only backfill of `index_ohlc.parquet` from `ind_close_all` per weekday. Fills NIFTYIT (and tops up others). ~1 to 1.5 years is enough for the index trend.

## Calibration (India-specific)

Thresholds are measured on this store, not imported from US studies: 4% mover, 5.0/0.5 ratio extremes, T2108, the 6% daily and 35%/65d quarter tiers, Bonde's 4% ADR floor, F&O and NSE sector universes. The opportunity engine's extension and run gates are **ATR-normalised**, so they self-adjust across volatility regimes (verified: the 2.5-ATR stretch fires on 9 to 14% of stock-days every year from 2019). The fixed-percent gates were set on a mostly-rising sample; re-check them every ~6 months and after any regime change. The Reference tab documents this.

## Colour grading

Cells are shaded by **fixed absolute thresholds**, not percentiles. Percentage columns on fixed 0 to 100 bands; count columns as a share of that day's universe; bands differ per universe where distributions genuinely differ. Down-metrics inverted so red always reads bearish. A percentile scale was rejected: it repaints on every regime change and hides the exact thing the dashboard exists to show. Long stretches of amber are the truth, not a fault.

## Data integrity

`ingest.py` writes `data/validation.txt` every run. Surface any non-empty finding before commentary. It checks advances+declines+unchanged vs universe count, duplicate date rows, byte-identical stale copies, universe size jumps over 10%, and missing Nifty close. Corporate actions are handled by chaining `close/prev_close`. The 200 DMA and 52-week stats are computed **per symbol on each symbol's own sessions**; a wide-panel rolling silently wiped them, since one NaN in the window nukes a strict `min_periods`, so never revert to a pivoted rolling.

## Files

```
scripts/ingest.py         download, adjust, compute breadth, validate; --backfill, --recompute, --index-backfill
scripts/render.py         CSV + JSON side-files to one self-contained HTML
scripts/opportunities.py  F&O long/short/fade engine -> opportunities.json (+ dated snapshots)
scripts/summary.py        Today-tab daily brief -> summary.json (+ data/summary/YYYY-MM-DD.json)
scripts/screen.py         Stage-2 / RS longs -> stocks.json
scripts/trader.py         VIX, index and F&O squeeze -> trader.json
scripts/changes.py        session-over-session alerts -> changes.json
scripts/frameworks.py     Screener CSVs in frameworks/ -> frameworks.json
.github/workflows/        breadth-daily.yml, recompute-breadth.yml, index-backfill.yml
references/               metric_definitions.md (all columns + JSON schemas), setup.md
```

Read `references/metric_definitions.md` before changing or explaining any metric. Do not redefine a metric from memory. The dashboard's Guide and Reference tabs carry the same definitions for the user; point them there rather than re-explaining.

