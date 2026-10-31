# Data Analytics Portfolio: Decision-Grade Analytics

Three projects built on real public data (two Australian public-sector, one retail). Each runs end to end from
source files with one command, carries automated QA tests, and separates what the data shows from what it cannot show.

| Project | Question | Data | Report |
|---|---|---|---|
| [1. Funding Is Not Access](project1_ndis_funding_access/README.md) | When NDIS funding is available, is it used? Where do SDA-eligible participants not use SDA? | NDIA participant datasets, 8 quarters (Sep-2024 to Jun-2026) | [NDIS_Funding_Access_Report.md](project1_ndis_funding_access/reports/NDIS_Funding_Access_Report.md) |
| [2. The Dashboard Reliability Lab](project2_dashboard_reliability/README.md) | When is a correctly computed KPI the wrong answer? | City of Melbourne customer service requests (2 datasets) | [KPI_Reliability_Report.md](project2_dashboard_reliability/reports/KPI_Reliability_Report.md) |
| [3. Availability to Action](project3_availability_to_action/README.md) | When sales hide demand lost to stockouts, which items should a planner review first, and does a stockout-adjusted ranking beat simple rules? | FreshRetailNet-50K: 4.85M store-product-days, 898 stores (Dingdong Inc.) | [Availability_to_Action_Report.md](project3_availability_to_action/reports/Availability_to_Action_Report.md) |

Stack: Python 3.11, DuckDB (SQL transformations), pandas, matplotlib, pytest; Power BI consumes the
CSV star schemas in each project's `outputs/powerbi/`. Each project ships a ready-built Power BI project
(`powerbi/*.pbip`: open in Power BI Desktop, press Refresh), generated from a Python spec by
[`tools/pbip_writer.py`](tools/pbip_writer.py).

```bash
pip install duckdb pandas openpyxl matplotlib pytest
python project1_ndis_funding_access/src/run_pipeline.py && python -m pytest -q project1_ndis_funding_access/tests
python project2_dashboard_reliability/src/run_pipeline.py
python project3_availability_to_action/scripts/download_data.py && python project3_availability_to_action/src/run_pipeline.py
python project1_ndis_funding_access/powerbi/build_pbip.py && python project2_dashboard_reliability/powerbi/build_pbip.py
python project3_availability_to_action/powerbi/build_pbip.py
```
