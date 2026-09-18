---
name: market-breadth
description: Render and interpret an Indian market-breadth and trading-opportunity dashboard (Stockbee-style market monitor plus an F&O opportunity engine) from NSE end-of-day data. It carries 20 universes (8 market-cap plus 12 sector), a top action panel with F&O-first posture and 2019-based signal base rates, and an Opportunities tab of ranked long / short / fade F&O ideas exportable to TradingView. ALWAYS use this skill when the user types /market-breadth, asks for market breadth, a breadth dashboard, the market monitor, advance-decline, 4% movers, percentage of stocks above their 10/20/30wk/50/200 DMA, new 52-week highs vs lows, whether the market is broadening or narrowing, whether a Nifty move is confirmed by participation, what to trade in Nifty / Bank Nifty / stock F&O today, or asks to update, refresh, or backfill their breadth or index history.
---

# Market Breadth + Opportunity Dashboard

## Daily routine (tell the user this)

There is nothing to run. A GitHub Actions job (`breadth-daily.yml`) is scheduled for 19:15 IST Mon to Fri, but GitHub's free scheduler delays it, so runs actually land and commit around **22:45 to 23:16 IST**. The user's only daily step is to type `/market-breadth` any time after **23:30 IST**. Only touch the Actions tab if a trading day passes with no new commit by the next morning, or if this skill reports stale data.

## Mode: refresh and render (the default)

The daily job already wrote the data. Pull the CSV **and every JSON side-file** (they feed the Trader, Opportunities, Screen, Frameworks and Changes tabs, and the top panel), then render. Missing side-files render as empty tabs, so never skip them.

```bash
mkdir -p ~/mb/data && cd ~/mb
B="https://raw.githubusercontent.com/bobbythomas-create/market-breadth/main"
for f in breadth_history.csv validation.txt stocks.json trader.json changes.json frameworks.json opportunities.json; do
  curl -sL "$B/data/$f" -o "data/$f"
done
curl -sL "$B/scripts/render.py" -o scripts/render.py
python3 scripts/render.py --csv data/breadth_history.csv --out /mnt/user-data/outputs/breadth_dashboard.html --rows 250
```

Prior-day opportunity snapshots (for a session the user missed) live at `data/opportunities/YYYY-MM-DD.json`; the Opportunities tab loads them on demand. Per-count stock lists (52-week highs, 4% movers) live at `data/lists/YYYY-MM-DD.json`, last 90 sessions. Fetch only the one day asked for, never the folder.

`raw.githubusercontent.com` is reachable from the sandbox. `nseindia.com` is not, so never fetch prices or index data directly here; that runs in Actions.

**Freshness check before commenting.** If the latest session in the file is not the last completed NSE session, say the file is stale and by how many sessions. Do not carry the last row forward.

## What the dashboard shows now

- **Top action panel (server-rendered, on every tab).** An F&O-first action list (Nifty F&O, Bank Nifty F&O, stock F&O, rest) with a mechanical posture; then the breadth regime, the signal table with **2019-based forward-return base rates** (washout, washout+thrust, thrust, bear/bull divergence, each with its current firing state), a sector rotation strip, a one-line posture and terse observations. This is the ten-second read.
- **Opportunities tab.** Ranked long / short / fade ideas across the single-stock F&O universe, each with a conviction tag (High/Med/Low = a ranking, NOT a win-rate) and a NEW/CONT flag, plus an index block (Nifty 50, Bank Nifty, and Nifty IT once its history is backfilled) and TradingView copy buttons. Fades are mean-reversion-after-expansion, counter-trend, capped lower.
- **20 universes.** 8 cap (ALL, LIQUID, FNO, NIFTY50, NIFTYNEXT50, MIDCAP150, SMALLCAP250, NIFTY500) plus 12 sector (SEC_*). Bank Nifty and Nifty IT are also in the top universe selector.
- **Primary tabs:** Today, Opportunities, Trader, Charts. Under "More": Screen, Table, Sectors, Segments, Regime, Scanner, Guide, Reference. Sectors and Segments have per-group "copy F&O to TradingView" chips.

## Non-negotiable token rules

1. **Never** print, cat, or read `breadth_history.csv` into context. Scripts read it, not you.
2. `render.py` prints a one-line-per-universe summary. That summary plus the panel content is all you need for commentary.
3. If you must inspect data, use pandas in a script and print only aggregates or `.tail(5)`.
4. On a daily refresh only new rows are appended. Do not regenerate history unless asked.

Budget: a refresh and render should cost well under 2,000 tokens of context.

## Reading it

Keep it terse: bullets or a short list, not prose. Anchor on:

- **% above 50 DMA** is the primary regime gauge. Above 60 broad, 25 to 45 corrective, below 12 a washout.
- **5-day / 10-day ratios**, India-calibrated. Indian 5-day median is near 1.7 (US near 1.0) because the EQ universe carries thin microcaps. Use above 5.0 for the aggressive extreme, below 0.5 for the defensive extreme. Bonde's US 2.0/0.5 do not transfer.
- **Breadth is most useful at extremes** and is asymmetric: bearish extremes call bottoms well, bullish extremes call tops poorly. Do not over-read mid-range readings.
- **Net 4% movers**: a day above 10% of the universe up with a 3:1 ratio is a thrust, the signature of a durable low.
- **Base rates** are the real probabilities and only count when a signal fires. Conviction tags on stocks are rankings, not win-rates. Say this plainly; do not attach fake percentages.
- **ALL vs NIFTY50/SMALLCAP gap** is usually the most tradeable observation.

Say what the numbers show, what would flip the read, and the mechanical posture. Do not predict specific levels. This is the user's private research tool, so a posture is fine; frame it as research, not advice.

## Workflows

- **`breadth-daily.yml`** (scheduled): ingest (NSE UDiFF bhavcopy CM+FO + `ind_close_all` index OHLC) then frameworks, screen, trader, changes, opportunities, render, commit.
- **`recompute-breadth.yml`** (manual button): rebuild the ENTIRE breadth history from the price store after any `ingest.py` change (e.g. the 200 DMA fix, the 30-week line). Re-runnable.
- **`index-backfill.yml`** (manual button, input = start date): light index-only backfill of `index_ohlc.parquet` from `ind_close_all` per weekday. Fills NIFTYIT (and tops up others). ~1 to 1.5 years is enough for the index trend.

## Calibration (India-specific)

Thresholds are measured on this store, not imported from US studies: 4% mover, 5.0/0.5 ratio extremes, T2108, the 6% daily and 35%/65d quarter tiers, Bonde's 4% ADR floor, F&O and NSE sector universes. The opportunity engine's extension and run gates are **ATR-normalised**, so they self-adjust across volatility regimes (verified: the 2.5-ATR stretch fires on 9 to 14% of stock-days every year from 2019). The fixed-percent gates were set on a mostly-rising sample; re-check them every ~6 months and after any regime change. The Reference tab documents this.

## Colour grading

Cells are shaded by **fixed absolute thresholds**, not percentiles. Percentage columns on fixed 0 to 100 bands; count columns as a share of that day's universe; bands differ per universe where distributions genuinely differ. Down-metrics inverted so red always reads bearish. A percentile scale was rejected: it repaints on every regime change and hides the exact thing the dashboard exists to show. Long stretches of amber are the truth, not a fault.

## Data integrity

`ingest.py` writes `data/validation.txt` every run. Surface any non-empty finding before commentary. It checks advances+declines+unchanged vs universe count, duplicate date rows, byte-identical stale copies, universe size jumps over 10%, and missing Nifty close. Corporate actions are handled by chaining `close/prev_close`. Known good-behaviour note: the 200 DMA and 52-week stats are computed per symbol on each symbol's own sessions (a wide-panel rolling silently wiped them, since one NaN in the window nukes a strict `min_periods`).

## Files

```
scripts/ingest.py         download, adjust, compute breadth, validate; --recompute, --index-backfill
scripts/render.py         CSV + JSON side-files to one self-contained HTML
scripts/opportunities.py  F&O long/short/fade engine -> opportunities.json (+ dated snapshots)
scripts/screen.py         Stage-2 / RS longs -> stocks.json
scripts/trader.py         VIX, index and F&O squeeze -> trader.json
scripts/changes.py        session-over-session alerts -> changes.json
scripts/frameworks.py     Screener CSVs in frameworks/ -> frameworks.json
.github/workflows/        breadth-daily.yml, recompute-breadth.yml, index-backfill.yml
references/               metric_definitions.md, setup.md
```

Read `references/metric_definitions.md` before changing or explaining any metric. Do not redefine a metric from memory.
