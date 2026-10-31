# Project 3: Availability to Action

An evidence-based inventory decision system on FreshRetailNet-50K (4.85 million store-product-days, 898 stores).

**Question:** when recorded sales do not show the demand lost to empty shelves, which store-product series should
a planner review first, and is a stockout-adjusted ranking actually better than the simple rules?

- **Decision Evidence Report (incl. executive brief):** [reports/Availability_to_Action_Report.md](reports/Availability_to_Action_Report.md)
- **Project Requirements Document:** [docs/PRD.md](docs/PRD.md)
- **Metric dictionary and business rules:** [docs/metric_dictionary.md](docs/metric_dictionary.md)
- **Power BI project:** [powerbi/Availability_to_Action.pbip](powerbi/Availability_to_Action.pbip) (open in Power BI Desktop, press Refresh)

## Results on held-out data

| Finding | Evidence |
|---|---|
| Recorded sales miss 35% of a sell-out day's demand; the recovery method cuts the error to 16% | Masking test on 806,550 real in-stock days |
| Each store's top-10 list holds 42.4% of next-week lost demand, vs 39.5% for 'top sellers first' and 28.5% for 'most frequent stockouts first' | Holdout week, 898 stores |
| The proposed score beats the simple 28-day lost-demand rule by only 0.7 points (95% CI 0.4 to 0.9) | Store bootstrap; B is good enough to deploy |
| 'Most frequent stockouts first' is the better list for empty-shelf hours, not for lost units | Model-free stockout-hour metric |
| Low-confidence rows realise 54% of their expected loss, High-confidence rows 86% | Confidence calibration |

Not claimed: order quantities, savings or causes. The data has no on-hand stock, lead times, costs or prices.

## Run

```bash
pip install duckdb pandas matplotlib pytest
python scripts/download_data.py      # 115 MB from Hugging Face, only if data/raw is empty
python src/run_pipeline.py           # ~70 s: rebuild, DQ, backtest, exports, tests, report, QA log
python powerbi/build_pbip.py         # regenerate the Power BI project from the CSVs
```

The pipeline exits non-zero if a critical DQ rule or any test fails.

## Components

| Component | Code | Output tables |
|---|---|---|
| Business decision definition | `docs/PRD.md` | |
| Data foundation and quality | `src/ingest.py`, `sql/01_staging.sql`, `src/dq.py` | `gov_source_manifest`, `qa_dq_results`, `qa_row_lineage` |
| Demand and risk analytics | `src/recovery.py` (recovery per decision day, masking test) | `rec_daily`, `an_masking_summary` |
| Decision prioritisation | `src/prioritise.py` (A1, A2, B, P; tiers, reasons, checks) | `decision_list` |
| Decision evaluation | `src/evaluate.py` (backtest, variant choice, bootstrap, forecast and tier checks) | `an_method_summary`, `an_eval_fold`, `an_bootstrap`, `an_forecast_check`, `an_confidence_check` |
| Reporting governance and handover | `config/params.json`, `src/export.py`, `src/report.py`, `docs/` | `outputs/powerbi/*`, `outputs/governance/*` |

## Automated tests (`tests/test_pipeline.py`)

| Area | Tests |
|---|---|
| Data integrity | grain and panel; known issues detected (DQ06 to DQ09); DQ rule fires on bad input; row lineage per fold |
| Calculations | recovered >= recorded, observed days unchanged, coverage in [0, 1]; masking ordering; capture recomputed in pandas; oracle is the ceiling |
| Leakage and design | features only from days before T; holdout is the eval split and unused for selection; recovery method chosen on a development fold |
| Decision outputs | every store has a list with reasons and checks; exports present; report makes no causal or savings claim |

## Change control

Every run appends parameter version, source hashes, DQ status, holdout results and test summary to
`outputs/governance/qa_log.jsonl`. A rule change = edit `config/params.json`, bump `version`, add a `change_log` entry,
rerun. `outputs/powerbi/fact_series_day.csv` (24 MB) and the parquet sources are rebuilt locally, not committed.
