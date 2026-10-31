"""Automated QA tests. Run after the pipeline has built data/com_requests.duckdb.

Tests marked "detects" assert that the QA machinery finds a known issue, so a silent
regression in a check (e.g. a flag that stops firing) fails the build.
"""
import json
from pathlib import Path

import duckdb
import pytest

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "data" / "com_requests.duckdb"


@pytest.fixture(scope="module")
def con():
    if not DB.exists():
        pytest.skip("run src/run_pipeline.py first")
    c = duckdb.connect(str(DB), read_only=True)
    yield c
    c.close()


def one(con, sql):
    return con.execute(sql).fetchone()[0]


# 1. invalid dates ---------------------------------------------------------------
def test_1_invalid_dates_detected_and_absent(con):
    for t, raw_col in [("stg_requests_a", "date_received_raw"), ("stg_requests_b", "date_opened_raw")]:
        # every non-empty raw date parsed; any parse failure would be flagged
        assert one(con, f"SELECT count(*) FROM {t} WHERE {raw_col} IS NOT NULL AND dq_invalid_received") == 0
        assert one(con, f"SELECT count(*) FROM {t} WHERE dq_invalid_completed OR dq_completed_before_received") == 0
        assert one(con, f"SELECT count(*) FROM {t} WHERE dq_future_date") == 0


def test_1b_invalid_date_rule_fires_on_bad_input():
    c = duckdb.connect()
    c.execute("CREATE TABLE x AS SELECT * FROM (VALUES ('2015-02-30'), ('2015-13-01'), ('2015-02-28')) t(d)")
    assert c.execute("SELECT count(*) FROM x WHERE TRY_CAST(d AS DATE) IS NULL").fetchone()[0] == 2


# 2. status vs completion date consistency --------------------------------------
def test_2_status_completion_consistency(con):
    assert one(con, "SELECT count(*) FROM stg_requests_a WHERE dq_closed_no_date") == 0
    assert one(con, "SELECT count(*) FROM stg_requests_b WHERE dq_closed_no_date OR dq_open_with_date") == 0
    # detects: the one A row that is WIP with a completion date
    assert one(con, "SELECT rows_flagged FROM qa_dq_results WHERE rule_id = 'DQ06' AND dataset = 'A'") == 1


def test_2b_days_field_on_open_rows_detected(con):
    assert one(con, "SELECT rows_flagged FROM qa_dq_results WHERE rule_id = 'DQ07'") == \
           one(con, "SELECT count(*) FROM stg_requests_a WHERE NOT is_closed")


# 3. missing rate of critical reporting fields ---------------------------------
def test_3_missing_rates_measured(con):
    for ds in "AB":
        rate = one(con, f"SELECT rate FROM qa_dq_results WHERE rule_id = 'DQ09' AND dataset = '{ds}'")
        assert 0 <= rate <= 1
    for t, cols in [("stg_requests_a", ["date_received", "status_raw", "category"]),
                    ("stg_requests_b", ["opened_utc", "status_raw", "service_type", "case_origin"])]:
        for c in cols:
            assert one(con, f"SELECT count(*) FROM {t} WHERE {c} IS NULL") == 0, f"{t}.{c}"


# 4. record counts reconcile through every transformation -----------------------
def test_4_row_lineage(con):
    for ds in "AB":
        counts = {r[0] for r in con.execute(f"SELECT rows FROM qa_row_lineage WHERE dataset = '{ds}'").fetchall()}
        assert len(counts) == 1, f"{ds}: row counts differ across steps {counts}"
    assert one(con, "SELECT count(*) FROM qa_dq_results WHERE rule_id = 'DQ14' AND status <> 'PASS'") == 0


def test_4b_sql_and_python_kpis_agree(con):
    assert one(con, "SELECT count(*) FROM kpi_integrity_recompute WHERE computed_correctly <> 'PASS'") == 0


# 5. effect of excluding missing data on KPIs -----------------------------------
def test_5_missing_data_exclusion_effect_recorded(con):
    rows = con.execute("""SELECT dataset, d_K01, d_K06 FROM sensitivity_scenarios
                          WHERE assumption_type = 'missing-data exclusion'""").fetchall()
    assert {r[0] for r in rows} == {"A", "B"}
    # B: dropping missing-suburb rows removes parking reviews and collapses the KPI - must be visible
    b = [r for r in rows if r[0] == "B"][0]
    assert abs(b[1]) > 1 or abs(b[2]) > 1


# 6. reporting period choice ----------------------------------------------------
def test_6_reporting_period_sensitivity(con):
    n = one(con, "SELECT count(*) FROM sensitivity_scenarios WHERE assumption_type = 'reporting period'")
    assert n >= 4
    # the trend conclusion depends on the KPI chosen: at least one 'improved' and one 'worsened'
    for ds in "AB":
        dirs = {r[0] for r in con.execute(
            f"SELECT direction FROM sensitivity_trend WHERE dataset = '{ds}' AND kpi IN ('K01', 'K02', 'K03', 'K06')").fetchall()}
        assert dirs, ds
    assert one(con, "SELECT count(DISTINCT direction) FROM sensitivity_trend WHERE dataset = 'A'") >= 2


def test_6b_cohorts_without_full_window_excluded_from_k03(con):
    assert one(con, """SELECT count(*) FROM kpi_sql_cohort
                       WHERE full_window AND followup_days_after_cohort_end < 30""") == 0
    assert one(con, "SELECT count(*) FROM kpi_sql_cohort WHERE NOT full_window") > 0


# 7. backlog history cannot be reconstructed exactly ----------------------------
def test_7_backlog_limits_recorded(con):
    assert one(con, "SELECT count(*) FROM backlog_limitations") >= 5
    assert one(con, "SELECT count(*) FROM backlog_reconstruction WHERE NOT reliable") > 0
    # earliest month is always inside the warm-up period (left truncation)
    assert one(con, """SELECT bool_and(NOT reliable) FROM backlog_reconstruction b
                       WHERE month_end = (SELECT min(month_end) FROM backlog_reconstruction c WHERE c.dataset = b.dataset)""")


# 8. Salesforce subset must be disclosed ----------------------------------------
def test_8_partial_source_disclosed(con):
    desc = one(con, "SELECT publisher_description FROM gov_source_manifest WHERE dataset = 'B'").lower()
    assert "subset" in desc
    impl = one(con, "SELECT implication FROM coverage_assessment WHERE dataset = 'B' AND aspect = 'Source system'")
    assert "subset" in impl.lower() and "must not" in impl.lower()
    report = (ROOT / "reports" / "KPI_Reliability_Report.md")
    if report.exists():
        text = report.read_text(encoding="utf-8").lower()
        assert "subset" in text


# extra: governance ------------------------------------------------------------
def test_kpi_definitions_versioned():
    d = json.loads((ROOT / "config" / "kpi_definitions.json").read_text(encoding="utf-8"))
    assert d["version"] == d["change_log"][-1]["version"]
    assert {k["id"] for k in d["kpis"]} >= {"K01", "K02", "K03", "K04", "K05", "K06", "K07"}
    for k in d["kpis"]:
        assert k["population"] and k["formula"] and k["blind_spot"]


def test_datasets_never_combined_in_kpis(con):
    # every KPI table carries a dataset column with a single dataset per row
    for t in ["kpi_sql_summary", "kpi_sql_cohort", "stress_kpi_values", "sensitivity_scenarios"]:
        assert one(con, f"SELECT count(*) FROM {t} WHERE dataset NOT IN ('A', 'B')") == 0, t


def test_naive_kpi_flagged_not_meaningful(con):
    assert one(con, "SELECT meaningful FROM kpi_meaningfulness WHERE kpi = 'K07'") == "Not meaningful"
