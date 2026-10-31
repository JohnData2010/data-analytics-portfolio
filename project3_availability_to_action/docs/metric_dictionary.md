# Metric dictionary and business rules

Parameters are versioned in [config/params.json](../config/params.json). Column-level descriptions of every
exported table are in [outputs/governance/data_dictionary.csv](../outputs/governance/data_dictionary.csv).

## Business rules

| Rule | Definition | Why |
|---|---|---|
| Selling window | Hours 06:00 to 21:59 (16 hours); hourly list positions 7 to 22 | Matches the publisher's `stock_hour6_22_cnt` exactly (DQ04); overnight flags carry over from the evening (DQ07) |
| Stockout hour | Hourly stock flag = 1 inside the selling window | Publisher annotation; treated as evidence, not proof (DQ06) |
| Partial stockout day | 1 to 15 stockout hours in the window | Some in-stock hours exist to recover from |
| Unavailable day | 16 stockout hours in the window | Nothing to recover from; may be a ranging decision (DQ08) |
| Decision day T | Planning happens at the start of day T; days T-28 to T-1 are visible | Mirrors a weekly planning cycle |
| Outcome week | Days T to T+6 | One planning horizon |
| Holdout | T = 91 (26-Jun-2024), the published eval split | Never used to choose a method or a setting |

## Metrics

| Metric | Formula | Grain | Notes |
|---|---|---|---|
| Recorded sales | Sum of hourly sales in the selling window | series-day | Normalised units |
| Hourly profile | Share of selling-window sales by hour on fully in-stock days, shrunk toward the third-category profile with a 5-day prior | series, per decision day | Category with no in-stock day: flat profile |
| Coverage | Sum of the profile over the hours the item was in stock | series-day | 1 on in-stock days, 0 on unavailable days |
| Typical in-stock day | Mean recorded sales on fully in-stock days, last 28 | series, per decision day | |
| Recovered demand | Recorded if no stockout; else max(recorded, in-stock sales + (1 - coverage) x typical day) | series-day | If no in-stock day in history: in-stock sales / coverage when coverage >= 15%, else recorded |
| Lost demand | Recovered - recorded on partial stockout days | series-day | The primary target; unavailable days are reported separately |
| Imputed share | (Recovered - recorded, all days) / recovered, last 28 | series | Feeds the confidence tier |
| Expected lost demand (P) | 7 x recency-weighted mean recovered demand x unserved share, share shrunk toward store x third category with m prior days | series | Half-life and m chosen on development folds |
| Confidence tier | High: imputed share <= 30% and >= 7 in-stock days. Low: imputed share > 60% or < 3 in-stock days. Otherwise Medium | series | Validated by realised / expected on the holdout |
| Store top-10 lost-demand capture | Sum over stores of next-week lost demand of the store's top 10 / total next-week lost demand | method x decision day | Primary backtest metric |
| Store top-10 stockout-hour capture | Same with stockout hours | method x decision day | Model-free check |
| Masking WAPE | Sum of absolute error / sum of true selling-window sales on masked in-stock days | estimator | Bias = signed error / truth |
