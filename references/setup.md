# Setup

## Why the work is split

Computing % above 200 DMA for 2,000 stocks needs roughly 250 sessions x 2,000 symbols of history.
No chat session can fetch or hold that. So a small job does the arithmetic on a schedule and leaves
behind one narrow CSV, about 35 columns and one row per session per universe. The chat only ever
reads that CSV.

NSE and Chartink are not reachable from the Claude sandbox. `raw.githubusercontent.com` is. That is
the whole reason a GitHub repo sits in the middle.

## Option A: GitHub Actions (recommended)

A GitHub repository can run a scheduled job on GitHub's own servers. No machine of yours needs to be
switched on. The job checks out the repo, runs the ingest script, and commits the updated CSV back.
Public repos get unlimited minutes; private repos get 2,000 free minutes a month and this job uses
about one minute a day.

1. Create a repo, e.g. `market-breadth`. Private is fine.
2. Copy `scripts/`, `references/` and `SKILL.md` into it.
3. Copy `assets/breadth-daily.yml` to `.github/workflows/breadth-daily.yml`.
4. Settings, Actions, General, Workflow permissions, set to **Read and write**.
5. Actions tab, run the workflow manually once with `backfill` set to `2024-04-01`.
6. Confirm `data/breadth_history.csv` and `data/validation.txt` appear in the repo.

**Known risk, stated plainly.** NSE sometimes blocks datacentre IP ranges, and GitHub runners sit in
Azure. If the workflow returns empty files, NSE is refusing the runner. Fallbacks, in order:
run the same script on your own machine on a schedule, or point the workflow at a self-hosted runner
on a machine at home. The scripts are identical in all three cases.

## Option B: your own machine

```bash
pip install pandas pyarrow requests
python3 scripts/ingest.py --backfill 2024-04-01     # once
python3 scripts/ingest.py                            # daily, after 19:00 IST
```

Schedule it with cron on macOS or Linux, or Task Scheduler on Windows, at 19:15 IST. Then either push
`data/breadth_history.csv` to a repo, or upload it to the chat when you want the dashboard.

## Option C: keep feeding Chartink by hand

Workable, and it is what the existing workbook does, but it cannot produce % above 40 DMA, the
moving-average stacking columns, or the F&O split without new Chartink scans, and every manual paste
is a chance to duplicate a row. Use it only as a cross-check.

## Cross-checking against the existing workbook

The old sheet and the new pipeline overlap from August 2024. Compare them once:

```python
import pandas as pd
new = pd.read_csv("data/breadth_history.csv", parse_dates=["date"]).query("universe=='ALL'")
old = pd.read_excel("Market_Breadth_Trade_Sensei_MAIN_20thFeb.xlsx", sheet_name="Market Breadth Dashboard")
# align dates, then compare up_4pct and down_4pct only; the DMA counts use different universes
```
Expect differences of a few percent from series filters and Chartink's own universe. Large gaps mean
one of the two is wrong, and the bhavcopy is the one with an audit trail.
