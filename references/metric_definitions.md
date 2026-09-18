# Metric definitions

Covers `data/breadth_history.csv` (one row per date, universe) and the JSON
side-files the dashboard reads. Read this before changing or explaining any metric.

## Universes (20)

One row per session per universe. Cap universes: `ALL` (NSE series EQ only, ~2,000 to 2,600 names), `LIQUID` (turnover and price floor), `FNO` (symbols with a single-stock future or option that day, point-in-time from the derivatives bhavcopy), `NIFTY50`, `NIFTYNEXT50`, `MIDCAP150`, `SMALLCAP250`, `NIFTY500`. Sector universes: `SEC_AUTO`, `SEC_BANK`, `SEC_ENERGY`, `SEC_FINSRV`, `SEC_FMCG`, `SEC_INFRA`, `SEC_IT`, `SEC_MEDIA`, `SEC_METAL`, `SEC_PHARMA`, `SEC_PSE`, `SEC_REALTY`. Constituent membership comes from NSE index files (`constituents.json`), current point-in-time.

## Price basis

- Raw daily fields come from NSE UDiFF CM bhavcopy: `ClsPric`, `PrvsClsgPric`, `TtlTradgVol`, `TtlTrfVal`, plus `OpnPric`, `HghPric`, `LwPric` (added so the opportunity engine can compute ranges, gaps and ATR).
- Daily return `r = ClsPric / PrvsClsgPric - 1`. NSE sets `PrvsClsgPric` to the adjusted base on an ex-date, so `r` is corporate-action clean.
- The adjusted series used for all moving averages, multi-day moves and 52-week extremes is the chained product of `(1 + r)`. It is a relative series, not a rupee price. Do not display it as a price.
- Returns with absolute value above 85% are treated as bad ticks and set to zero for the chain.
- **All rolling stats (MAs, 52-week high/low) are computed per symbol on that symbol's own consecutive sessions, then pivoted wide.** Computing them on the union-date wide panel is wrong: one NaN in the window nukes a strict `min_periods`, which silently wiped the 200 DMA for almost every symbol while the 50 DMA survived. Never revert to a pivoted rolling.

## breadth_history.csv columns

| Column | Definition |
|---|---|
| `universe_count` | Symbols that traded that day in the universe |
| `advances` / `declines` / `unchanged` | Sign of `r` |
| `up_4pct` / `down_4pct` / `net_4pct` | `r >= +4%` / `r <= -4%` close to close; net is the difference |
| `net4_5d` | Rolling 5-session sum of `net_4pct` |
| `ratio_5d` / `ratio_10d` | Up-to-down 4% mover ratio over 5 / 10 sessions. Stockbee's primary signal, India-calibrated (5.0 aggressive, 0.5 defensive) |
| `up_20pct_5d` / `down_20pct_5d`, `up_25pct_21d` / `down_25pct_21d`, `up_50pct_21d` / `down_50pct_21d` | Adjusted move over 5 or 21 sessions past the threshold |
| `pct_above_{10,20,40,50,150,200}dma` | Percent of symbols with enough history whose adjusted close is above that simple moving average. **150 = the 30-week Weinstein stage line** (added). 40 feeds T2108 |
| `cover_{w}dma` | How many symbols had enough history for that average. If far below `universe_count`, read the percentage with care |
| `pct_10dma_gt_20dma`, `pct_20dma_gt_50dma`, `pct_50dma_gt_200dma` | Percent whose MAs are stacked in that order. Slower and cleaner than raw percent-above |
| `pct_extended_50dma` / `extended_50dma` | Percent (and count) of symbols more than a set multiple above their 50 DMA. Froth gauge |
| `new_52w_high` / `new_52w_low` | Adjusted close at or within 0.1% of the 250-session extreme (needs 100+ sessions) |
| `nifty_close` / `nifty_chg_pct` | Nifty 50 close and session change, from `ind_close_all` |
| `div_bearish` | Nifty at a 20-session high while `pct_above_40dma` is not at its own 20-session high |
| `div_bullish` | Mirror at a 20-session low |
| `thrust` | `up_4pct` at least 10% of the universe and at least 3x `down_4pct` |

## index_ohlc.parquet

Per-index daily O/H/L/C from NSE `ind_close_all`. Indices carried: `NIFTY50`, `BANKNIFTY`, `INDIAVIX`, `NIFTYIT` (added; fill history with the `index-backfill` workflow). Feeds the Trader tab (VIX, index squeeze) and the opportunity engine's index block.

## opportunities.json (from opportunities.py)

Latest session plus dated snapshots under `data/opportunities/`. Scans the single-stock F&O universe.

- `longs` / `shorts` / `fades`: arrays of ideas, each `{s: symbol, side, cls: [signal classes], ret1, vm: volume multiple, rs: 1-99 RS percentile, stage, conv: 0-1 score, tag: High/Med/Low, why, state: NEW/CONT}`.
- **Conviction** = move size x volume confirmation x trend alignment, RS-tilted, direction-aware (an up move does not score a short). It is a **ranking, not a win-rate**.
- **Classes.** Longs: momentum burst, RS leader, Stage-2 break, episodic pivot. Shorts: breakdown, RS laggard, Stage-4 break, gap-down. Fades: overbought fade (>=2.5 ATR above the 20 DMA after a fast run, exhaustion bar) or oversold bounce (mirror). Fade gates are ATR-normalised for regime robustness.
- `index`: `[{tag, label, ret1, trend, vol_state, posture, ...}]` for Nifty 50, Bank Nifty, Nifty IT.
- `dates`: recent snapshot dates for the session selector.

## Derived reads used in the panel and Trader tab

- **Base rates.** For each breadth signal (washout `%>50DMA<12`, thrust, washout+thrust, bear/bull divergence), every occurrence since 2019 is de-clustered and forward Nifty returns measured at 20/60/120 sessions; the panel shows n, +60d median and hit-rate. Real probabilities, and only meaningful when the signal fires.
- **ATR percentile.** Where today's ATR sits against the symbol's own last ~100 sessions. Low = coiled, high = already moving. A volatility clock, not a direction.
- **% of price (ADR).** Average daily range as a fraction of price. Bigger = more room to pay multiples of risk; momentum methods want 4%+.

## Deliberate choices

- 10, 20, 40, 50, 150, 200 DMA all carried. 40 is the Stockbee intermediate gauge and feeds T2108; 150 is the 30-week Weinstein line; 50 and 200 are conventional.
- Counts and percentages both stored. Grade colours off percentages; counts break when the universe changes size.
- Series BE, BZ, SM, ST, GB excluded (surveillance, SME, bond).
- No liquidity or market-cap floor on ALL, by choice; use LIQUID or FNO for a liquid-only read.

