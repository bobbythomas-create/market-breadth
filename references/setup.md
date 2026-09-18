# Setup

## Why the work is split

Computing % above 200 DMA and the opportunity signals for 2,000+ stocks needs
roughly 250 sessions x 2,000 symbols of history. No chat session can fetch or hold
that. So scheduled jobs do the arithmetic and leave behind one narrow CSV (about 59
columns, one row per session per universe) plus small JSON side-files. The chat only
reads those.

NSE is not reachable from the Claude sandbox. `raw.githubusercontent.com` is. That is
why a GitHub repo sits in the middle.

## The daily pipeline (breadth-daily.yml)

Scheduled 19:15 IST Mon to Fri, but GitHub's scheduler delays it, so it lands ~22:45 to
23:16 IST. Steps, in order, each committing its output:

1. `ingest.py` downloads NSE UDiFF bhavcopy (CM + FO) and `ind_close_all` index OHLC, maintains `prices.parquet` (adjusted), and writes `breadth_history.csv` + `validation.txt`.
2. `frameworks.py` reads any Screener CSVs in `frameworks/` into `frameworks.json`.
3. `screen.py` writes the Stage-2 / RS longs to `stocks.json`.
4. `trader.py` writes VIX, index and F&O squeeze to `trader.json`.
5. `changes.py` writes session-over-session alerts to `changes.json`.
6. `opportunities.py` writes ranked long/short/fade F&O ideas to `opportunities.json` (+ dated snapshots under `data/opportunities/`).
7. `render.py` builds `dashboard.html`.

## First-time setup (GitHub Actions, recommended)

1. Create a repo `market-breadth` (private is fine). Copy `scripts/`, `references/`, `SKILL.md`, `frameworks/` and `requirements.txt` into it.
2. Copy the three workflow files into `.github/workflows/`.
3. Settings, Actions, General, Workflow permissions, set to **Read and write**.
4. Actions tab, run `breadth-daily` manually once with backfill `2024-04-01` (the 200 DMA needs ~1 year of runway).
5. Confirm `data/breadth_history.csv`, `validation.txt` and the JSON side-files appear.

Deep history to 2019 was a one-time Kite Connect backfill of `prices.parquet` and `index_ohlc.parquet`. **Kite is now decommissioned and its key rotated** (zero-exposure standard). Do not attempt a Kite pull. The nightly pipeline runs entirely on free NSE bhavcopy.

**Known risk.** NSE sometimes blocks datacentre IPs, and GitHub runners sit in Azure. If a workflow returns empty files, NSE is refusing the runner: fall back to running the scripts on your own machine or a self-hosted runner. The scripts are identical.

## The manual buttons

- **recompute-breadth.yml** rebuilds the ENTIRE `breadth_history.csv` from `prices.parquet` under the current `ingest.py` logic. Run it after any metric change (e.g. the per-symbol 200 DMA fix, the 30-week line). ~30 to 60 min. It does not fetch new sessions.
- **index-backfill.yml** (input: start date) does a light index-only backfill of `index_ohlc.parquet` from `ind_close_all` per weekday, to fill a newly added index such as NIFTYIT. ~1 to 1.5 years is enough for the 50/200 DMA index trend.

## Your own machine (alternative)

```bash
pip install pandas pyarrow requests
python3 scripts/ingest.py --backfill 2024-04-01   # once
python3 scripts/ingest.py                          # daily, after bhavcopy is out (~19:00 IST)
# then the same frameworks/screen/trader/changes/opportunities/render steps
```

Schedule with cron or Task Scheduler. Push the repo, or upload `breadth_history.csv` plus the JSON side-files to the chat.

## Cross-checking

The bhavcopy pipeline has an audit trail; a Chartink or workbook feed does not. When counts disagree, trust the bhavcopy. Expect a few percent difference from series filters and universe definitions.

