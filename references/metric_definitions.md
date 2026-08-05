# Metric definitions

Every column in `data/breadth_history.csv`. One row per (date, universe).
Universe values: `ALL` = NSE series EQ only. `FNO` = symbols with a single-stock
future or option on that date, taken from the derivatives bhavcopy, so the list
is point-in-time and never survivorship-biased.

## Price basis

- Raw daily fields come from NSE UDiFF CM bhavcopy: `ClsPric`, `PrvsClsgPric`, `TtlTradgVol`, `TtlTrfVal`.
- Daily return `r = ClsPric / PrvsClsgPric - 1`. NSE sets `PrvsClsgPric` to the adjusted base price on
  an ex-date for splits, bonuses and consolidations, so `r` is already corporate-action clean.
- The adjusted series used for all moving averages, 21-day and 5-day moves and 52-week extremes is the
  chained product of `(1 + r)` from the symbol's first day in the store. It is a relative series, not
  a rupee price. Do not display it as a price.
- Returns with absolute value above 85% are treated as bad ticks and set to zero for the chain, and
  the affected symbol-days are still counted in advances or declines as they came.

## Columns

| Column | Definition |
|---|---|
| `universe_count` | Symbols that traded that day in the universe |
| `advances` / `declines` / `unchanged` | Sign of `r` |
| `up_4pct` / `down_4pct` | `r >= +4%` / `r <= -4%`, close to close, not intraday |
| `net_4pct` | `up_4pct - down_4pct` |
| `net4_5d` | Rolling 5-session sum of `net_4pct` |
| `up_20pct_5d` / `down_20pct_5d` | Adjusted move over the last 5 sessions |
| `up_25pct_21d` / `down_25pct_21d` | Adjusted move over the last 21 sessions ("monthly") |
| `up_50pct_21d` / `down_50pct_21d` | Same window, 50% threshold |
| `pct_above_{10,20,40,50,200}dma` | Percent of symbols with enough history whose adjusted close is above that simple moving average |
| `cover_{w}dma` | How many symbols had enough history for that average. If `cover` is far below `universe_count`, the percentage is computed on a subset and should be read with care |
| `pct_10dma_gt_20dma`, `pct_20dma_gt_40dma`, `pct_50dma_gt_200dma` | Percent of symbols whose moving averages are stacked in that order. Slower and cleaner than the raw percent-above series |
| `new_52w_high` / `new_52w_low` | Adjusted close at or within 0.1% of the 250-session extreme. Needs at least 100 sessions of history for that symbol |
| `nifty_close` | Nifty 50 close from the NSE indices close file |
| `nifty_chg_pct` | Session change in percent |
| `div_bearish` | Nifty at a 20-session high while `pct_above_40dma` is not at its own 20-session high |
| `div_bullish` | Nifty at a 20-session low while `pct_above_40dma` is not at its own 20-session low |
| `thrust` | `up_4pct` at least 10% of the universe and at least 3x `down_4pct` |

## Deliberate choices

- 10, 20, 40, 50 and 200 DMA are all carried. 40 is the Stockbee-style intermediate gauge; 50 and 200
  are the conventional ones. 21 DMA was dropped as it is functionally identical to 20.
- Counts and percentages are both stored. Grade colours off percentages, since counts break the moment
  the universe changes size.
- Series BE, BZ, SM, ST and GB are excluded. They are surveillance, SME and bond series and they add
  noise to every threshold count.
- No liquidity or market-cap floor is applied to the ALL universe, by choice. Use the FNO view when a
  liquid-only read is wanted.
