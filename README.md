# Data Analytics Portfolio: Decision-Grade Public Sector Analytics

Two projects built on real Australian public data. Both run end to end from source files with one
command, carry automated QA tests, and separate what the data shows from what it cannot show.

| Project | Question | Data | Report |
|---|---|---|---|
| [1. Funding Is Not Access](project1_ndis_funding_access/README.md) | When NDIS funding is available, is it used? Where do SDA-eligible participants not use SDA? | NDIA participant datasets, 8 quarters (Sep-2024 to Jun-2026) | [NDIS_Funding_Access_Report.md](project1_ndis_funding_access/reports/NDIS_Funding_Access_Report.md) |
| [2. The Dashboard Reliability Lab](project2_dashboard_reliability/README.md) | When is a correctly computed KPI the wrong answer? | City of Melbourne customer service requests (2 datasets) | [KPI_Reliability_Report.md](project2_dashboard_reliability/reports/KPI_Reliability_Report.md) |

Stack: Python 3.11, DuckDB (SQL transformations), pandas, matplotlib, pytest; Power BI consumes the
CSV star schemas in each project's `outputs/powerbi/` (build guides in `powerbi/`).

```bash
pip install duckdb pandas openpyxl matplotlib pytest
python project1_ndis_funding_access/src/run_pipeline.py && python -m pytest -q project1_ndis_funding_access/tests
python project2_dashboard_reliability/src/run_pipeline.py
```
