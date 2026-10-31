"""Power BI star schema (outputs/powerbi/*.csv), data dictionary and the append-only QA log."""
import json
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "outputs"
PBI, GOV = OUT / "powerbi", OUT / "governance"
PARAMS = json.loads((ROOT / "config" / "params.json").read_text(encoding="utf-8"))
T = PARAMS["holdout_decision_day"]
LIST_N = 25                      # review-list rows exported per store (the dashboard shows the top 10)


def build_tables(con):
    con.execute(f"""
        CREATE OR REPLACE TABLE an_hour_profile AS
        SELECT i - 1 AS hour, i - 1 BETWEEN 6 AND 21 AS in_selling_window,
               avg(hours_stock_status[i]) AS oos_share, sum(hours_sale[i]) / sum(sale_amount) AS sales_share
        FROM raw_train, range(1, 25) t(i) GROUP BY i ORDER BY i;
        CREATE OR REPLACE TABLE fact_store_day AS
        SELECT r.day_idx, dd.dt, d.store_id, r.is_outcome, count(*) AS series,
               sum(r.window_sales) AS recorded, sum(r.rec) AS recovered,
               sum(r.lost) FILTER (WHERE r.method = 'partial') AS lost_partial,
               count(*) FILTER (WHERE r.method = 'unavailable') AS unavailable_series_days,
               sum(r.oos_hours) AS oos_hours, count(*) FILTER (WHERE r.oos_hours > 0) AS series_days_with_stockout
        FROM rec_daily r JOIN dim_series d USING (series_id) JOIN dim_date dd USING (day_idx)
        WHERE r.fold = {T} GROUP BY ALL;
        CREATE OR REPLACE TABLE dim_store AS
        SELECT store_id, any_value(city_id) AS city_id, count(*) AS series FROM dim_series GROUP BY 1;
        CREATE OR REPLACE TABLE fact_series_day AS        -- listed series only (store top 10)
        SELECT r.series_id, r.day_idx, dd.dt, r.is_outcome, r.method, r.oos_hours, r.first_oos_hour,
               r.window_sales AS recorded, r.rec AS recovered, r.lost
        FROM rec_daily r JOIN dim_date dd USING (day_idx)
        WHERE r.fold = {T} AND r.series_id IN (SELECT series_id FROM decision_list WHERE in_store_top_n);
    """)


EXPORTS = {
    "dim_series": "SELECT * FROM dim_series",
    "dim_store": "SELECT * FROM dim_store",
    "dim_date": f"SELECT *, day_idx >= {T} AS is_outcome_week FROM dim_date WHERE day_idx >= {T - 28}",
    "decision_list": f"SELECT * FROM decision_list WHERE store_rank <= {LIST_N}",
    "fact_store_day": "SELECT * FROM fact_store_day",
    "fact_series_day": "SELECT * FROM fact_series_day",
    "an_method_summary": "SELECT * FROM an_method_summary",
    "an_eval_fold": "SELECT * FROM eval_fold",
    "an_bootstrap": "SELECT * FROM an_bootstrap",
    "an_variant_selection": "SELECT * FROM an_variant_selection",
    "an_masking_summary": "SELECT * FROM an_masking_summary",
    "an_forecast_check": "SELECT * FROM an_forecast_check",
    "an_confidence_check": "SELECT * FROM an_confidence_check",
    "an_hour_profile": "SELECT * FROM an_hour_profile",
    "qa_dq_results": "SELECT * FROM qa_dq_results",
    "qa_row_lineage": "SELECT * FROM qa_row_lineage",
    "gov_source_manifest": "SELECT * FROM gov_source_manifest",
}

DESCRIPTIONS = {
    "series_id": "store_id * 10000 + product_id: one store-product time series",
    "store_rank": "Rank within the store by the proposed score (1 = review first)",
    "global_rank": "Rank across all 50,000 series",
    "in_store_top_n": "On the store's top-10 review list",
    "expected_lost_7d": "Proposed score: expected lost demand next 7 days (normalised units)",
    "obs_28": "Recorded selling-window sales, last 28 days", "rec_28": "Recovered demand, last 28 days",
    "lost_28": "Recovered minus recorded demand on partial stockout days, last 28 days",
    "imputed_share": "Share of 28-day recovered demand that is imputed (incl. unavailable days)",
    "hidden_vs_recorded": "lost_28 / obs_28",
    "oos_days_28": "Days with any stockout hour in the selling window, last 28 days",
    "oos_days_7": "Same, last 7 days", "oos_hours_28": "Stockout hours in the selling window, last 28 days",
    "unavailable_days_28": "Days out of stock for the whole selling window, last 28 days",
    "conflict_days_28": "Days with sales recorded after the sell-out hour (DQ06), last 28 days",
    "instock_days": "Fully in-stock days in the last 28 (the basis of the hourly profile)",
    "typical_sellout_hour": "Median first stockout hour on partial stockout days",
    "confidence": "High / Medium / Low: how much of the demand estimate is imputed",
    "reasons": "Why the series is ranked where it is", "checks_before_acting": "What to verify before ordering",
    "outcome_lost": "Holdout week: realised lost demand on partial stockout days (estimated)",
    "outcome_oos_hours": "Holdout week: realised stockout hours (model-free)",
    "outcome_unavailable_days": "Holdout week: days out of stock all window",
    "outcome_recorded": "Holdout week: recorded sales",
    "recorded": "Recorded selling-window sales", "recovered": "Recovered demand", "lost": "recovered - recorded",
    "lost_partial": "Lost demand on partial stockout days", "method": "observed / partial / unavailable",
    "is_outcome": "Day falls in the holdout outcome week (26-Jun to 02-Jul-2024)",
}


def data_dictionary(con):
    rows = []
    for t in EXPORTS:
        for c, typ in con.execute(f"SELECT column_name, column_type FROM (DESCRIBE {EXPORTS[t]})").fetchall():
            rows.append(dict(table=t, column=c, type=typ, description=DESCRIPTIONS.get(c, "")))
    return pd.DataFrame(rows)


def run(con):
    build_tables(con)
    PBI.mkdir(parents=True, exist_ok=True)
    GOV.mkdir(parents=True, exist_ok=True)
    for name, sql in EXPORTS.items():
        con.execute(f"COPY ({sql}) TO '{(PBI / f'{name}.csv').as_posix()}' (HEADER, DELIMITER ',')")
    data_dictionary(con).to_csv(GOV / "data_dictionary.csv", index=False)
    print(f"exported {len(EXPORTS)} tables to outputs/powerbi")


def run_tests():
    xml = GOV / "test_results.xml"
    r = subprocess.run([sys.executable, "-m", "pytest", "-q", str(ROOT / "tests"), f"--junitxml={xml}"],
                       capture_output=True, text=True)
    print(r.stdout[-1500:])
    summary = r.stdout.strip().splitlines()[-1] if r.stdout.strip() else "no output"
    return {"exit_code": r.returncode, "summary": summary}


def log_run(con, tests, chosen):
    entry = dict(
        run_id=str(uuid.uuid4()), run_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        params_version=PARAMS["version"], proposed_variant=chosen,
        sources={r[0]: r[1] for r in con.execute("SELECT split, sha256 FROM gov_source_manifest").fetchall()},
        dq={r[0]: r[1] for r in con.execute("SELECT rule_id, status FROM qa_dq_results").fetchall()},
        holdout={r[0]: round(r[1], 4) for r in con.execute(
            "SELECT method, store_top10_lost_capture FROM an_method_summary WHERE role = 'holdout'").fetchall()},
        tests=tests["summary"],
    )
    with open(GOV / "qa_log.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, default=str) + "\n")
    return entry
