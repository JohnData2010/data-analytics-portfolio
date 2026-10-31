# Project 2: The Dashboard Reliability Lab

Decision-grade analytics and data-quality intelligence on City of Melbourne customer service requests.

**Question:** what happens when a dashboard is technically correct, but the business conclusion is wrong?

- **KPI Reliability Report + Decision Evidence Report:** [reports/KPI_Reliability_Report.md](reports/KPI_Reliability_Report.md)
- **KPI definitions (versioned):** [config/kpi_definitions.json](config/kpi_definitions.json)
- **Data dictionary:** [outputs/governance/data_dictionary.csv](outputs/governance/data_dictionary.csv)
- **QA log:** [outputs/governance/qa_log.jsonl](outputs/governance/qa_log.jsonl)
- **Power BI build guide:** [powerbi/PowerBI_build_guide.md](powerbi/PowerBI_build_guide.md)

## Run

```bash
pip install duckdb pandas matplotlib pytest
python scripts/download_data.py   # only if data/raw is empty
python src/run_pipeline.py        # rebuild everything, run tests, append QA log (~15 s)
```

The pipeline exits non-zero if any QA test fails.

## Modules

| Module | Code | Output tables |
|---|---|---|
| Data Quality Engine | `sql/01_staging.sql` (record flags), `src/dq_engine.py` (rules + KPI impact) | `qa_dq_results` |
| KPI Integrity Engine | `sql/02_kpi.sql` (SQL KPIs), `src/kpi_engine.py` (independent recompute, Kaplan-Meier, meaningfulness rules) | `kpi_integrity_recompute`, `kpi_meaningfulness`, `km_curves` |
| KPI Stress Testing | `src/stress_sensitivity.py` | `stress_kpi_values`, `stress_reversals`, `stress_rank_agreement` |
| Sensitivity Analysis | `src/stress_sensitivity.py` | `sensitivity_scenarios`, `sensitivity_trend` |
| Coverage Assessment | `src/coverage.py` | `coverage_assessment`, `backlog_reconstruction`, `backlog_limitations` |
| Reporting Governance | `src/ingest.py` (manifest, hashes), `src/governance.py` (dictionary, exports, QA log), `CHANGELOG.md` | `gov_source_manifest`, `outputs/governance/*` |
| Decision Evidence Report | `src/report.py` | `reports/KPI_Reliability_Report.md` |

## Automated QA tests (`tests/test_qa.py`)

| # | Requirement | Test |
|---|---|---|
| 1 | Invalid dates | `test_1_*` (plus a negative test that the rule fires on bad input) |
| 2 | Status vs completion date | `test_2_*` (detects the WIP row with a completion date; open rows carrying ages) |
| 3 | Missing rate of key fields | `test_3_missing_rates_measured` |
| 4 | Record counts through each transformation | `test_4_row_lineage`, `test_4b_sql_and_python_kpis_agree` |
| 5 | Effect of excluding missing data | `test_5_missing_data_exclusion_effect_recorded` |
| 6 | Reporting-period choice | `test_6_*` (incl. cohorts without a full window excluded) |
| 7 | Backlog history not reconstructable | `test_7_backlog_limits_recorded` |
| 8 | Salesforce subset disclosed | `test_8_partial_source_disclosed` |

## Change control

KPI definitions carry a version and change log; every run appends the definition version, source file
hashes, DQ results and test results to `qa_log.jsonl`. A KPI change = edit `kpi_definitions.json`, bump
`version`, add a `change_log` entry and a `CHANGELOG.md` line, rerun.
