"""Automated data and calculation checks. Run after the pipeline has built data/a2a.duckdb.

Tests marked "detects" assert that a check finds a known issue, so a silent regression in a rule (a flag that
stops firing) fails the build, not just a rule that starts firing.
"""
import json
import re
from pathlib import Path

import duckdb
import pytest

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "data" / "a2a.duckdb"
P = json.loads((ROOT / "config" / "params.json").read_text(encoding="utf-8"))
T = P["holdout_decision_day"]
FOLDS = P["dev_decision_days"] + [T]


@pytest.fixture(scope="module")
def con():
    if not DB.exists():
        pytest.skip("run src/run_pipeline.py first")
    c = duckdb.connect(str(DB), read_only=True)
    yield c
    c.close()


def one(con, sql):
    return con.execute(sql).fetchall()[0][0]


# data integrity ---------------------------------------------------------------
def test_grain_and_panel(con):
    assert one(con, "SELECT count(*) FROM stg_daily") == one(con, "SELECT count(*) FROM raw_train") + \
        one(con, "SELECT count(*) FROM raw_eval")
    for rule in ["DQ01", "DQ02", "DQ03", "DQ04", "DQ05", "DQ10"]:
        assert one(con, f"SELECT status FROM qa_dq_results WHERE rule_id = '{rule}'") == "PASS", rule


def test_known_issues_detected(con):
    # detects: overnight flags, sales after the sell-out hour, full-day stockouts, discount = 0
    for rule in ["DQ06", "DQ07", "DQ08", "DQ09"]:
        assert one(con, f"SELECT rows_flagged FROM qa_dq_results WHERE rule_id = '{rule}'") > 0, rule
    # the 24-hour stockout rate must exceed the selling-window rate, or DQ07's premise is gone
    assert one(con, "SELECT avg(oos_hours_24h / 24.0) - avg(oos_hours / 16.0) FROM stg_daily") > 0


def test_dq_rule_fires_on_bad_input():
    c = duckdb.connect()
    c.execute("""CREATE TABLE x AS SELECT * FROM (VALUES ([1, 1, 0], [0.0, 2.0, 1.0]), ([0, 1, 1], [1.0, 0.0, 0.0]))
                 t(s, h)""")
    flagged = c.execute("""SELECT count(*) FROM x WHERE list_sum(list_transform(h, lambda v, i:
                           CASE WHEN i > 1 AND s[i] = 1 AND s[i - 1] = 1 AND v > 0 THEN 1 ELSE 0 END)) > 0""").fetchall()[0][0]
    assert flagged == 1          # sales in a second consecutive out-of-stock hour; the sell-out hour alone is fine


def test_row_lineage_every_fold_complete(con):
    for fold in FOLDS:
        assert one(con, f"SELECT count(*) FROM rec_daily WHERE fold = {fold}") == 35 * 50000, fold
    assert one(con, "SELECT count(*) FROM decision_list") == 50000


# calculations -----------------------------------------------------------------
def test_recovery_invariants(con):
    assert one(con, "SELECT count(*) FROM rec_daily WHERE rec < window_sales - 1e-9 OR rec IS NULL") == 0
    assert one(con, "SELECT count(*) FROM rec_daily WHERE method = 'observed' AND abs(lost) > 1e-9") == 0
    assert one(con, "SELECT count(*) FROM rec_daily WHERE coverage < -1e-9 OR coverage > 1 + 1e-9") == 0


def test_masking_beats_recorded_sales(con):
    w = dict(con.execute("""SELECT estimator, wape FROM an_masking_summary
                            WHERE role LIKE 'holdout%' AND sellout_window = '1. all'""").fetchall())
    assert w["D. Coverage-weighted blend (used)"] < w["C. Hourly profile scaling"] < w["A. Recorded sales (no adjustment)"]


def test_capture_recomputed_independently(con):
    """SQL metric vs a direct recompute in pandas for the holdout proposed method."""
    chosen = one(con, "SELECT method FROM an_variant_selection WHERE chosen")
    sql_val = one(con, f"SELECT store_top10_lost_capture FROM eval_fold WHERE fold = {T} AND method = '{chosen}'")
    d = con.execute("SELECT store_rank, outcome_lost FROM decision_list").df()
    assert abs(d.loc[d.store_rank <= P["prioritisation"]["store_top_n"], "outcome_lost"].sum()
               / d.outcome_lost.sum() - sql_val) < 1e-9


def test_oracle_is_the_ceiling(con):
    for fold in FOLDS:
        r = dict(con.execute(f"SELECT method, store_top10_lost_capture FROM eval_fold WHERE fold = {fold}").fetchall())
        assert max(r.values()) == r["Oracle (hindsight)"], fold


# leakage and evaluation design --------------------------------------------------
def test_features_use_only_days_before_decision(con):
    assert one(con, "SELECT count(*) FROM rec_daily WHERE NOT is_outcome AND day_idx >= fold") == 0
    assert one(con, "SELECT count(*) FROM rec_daily WHERE is_outcome AND (day_idx < fold OR day_idx >= fold + 7)") == 0
    assert one(con, "SELECT count(*) FROM rec_daily WHERE NOT is_outcome AND day_idx < fold - 28") == 0


def test_holdout_is_eval_split_and_not_used_for_selection(con):
    assert one(con, f"SELECT min(split) || max(split) FROM stg_daily WHERE day_idx >= {T}") == "evaleval"
    assert T not in P["dev_decision_days"]
    assert all(d + 7 <= T for d in P["dev_decision_days"])
    # the variant table is built from development folds only
    assert one(con, "SELECT count(*) FROM an_variant_selection") == len(P["prioritisation"]["half_life_grid"]) * 2


def test_masking_method_chosen_on_development_fold():
    assert P["recovery"]["method_selection_fold"] in P["dev_decision_days"]


# decision outputs ---------------------------------------------------------------
def test_every_store_has_a_review_list(con):
    assert one(con, "SELECT count(DISTINCT store_id) FROM decision_list WHERE in_store_top_n") == \
        one(con, "SELECT count(DISTINCT store_id) FROM dim_series")
    assert one(con, "SELECT count(*) FROM decision_list WHERE in_store_top_n AND (reasons = '' OR reasons IS NULL)") \
        <= 0.05 * one(con, "SELECT count(*) FROM decision_list WHERE in_store_top_n")
    assert one(con, "SELECT count(*) FROM decision_list WHERE checks_before_acting NOT LIKE '%lead time%'") == 0


def test_report_makes_no_unverified_causal_claim():
    text = (ROOT / "reports" / "Availability_to_Action_Report.md").read_text(encoding="utf-8")
    for phrase in [r"\bdue to stockouts\b", r"\bcaused by\b", r"\bsav(e|es|ing|ings) \$", r"\breduce[sd]? stockouts by\b"]:
        assert not re.search(phrase, text, re.I), phrase
    assert "Association, not cause" in text and "No cost or saving claim" in text


def test_exports_present(con):
    pbi = ROOT / "outputs" / "powerbi"
    for name in ["decision_list", "fact_store_day", "dim_series", "an_method_summary", "qa_dq_results"]:
        assert (pbi / f"{name}.csv").exists(), name
