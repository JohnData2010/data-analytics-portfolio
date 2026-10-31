"""Automated checks on the built DuckDB. Run after `python src/run_pipeline.py`."""
from pathlib import Path

import duckdb
import pytest

DB = Path(__file__).resolve().parents[1] / "data" / "ndis.duckdb"


@pytest.fixture(scope="module")
def con():
    if not DB.exists():
        pytest.skip("run src/run_pipeline.py first")
    c = duckdb.connect(str(DB), read_only=True)
    yield c
    c.close()


def one(con, sql):
    return con.execute(sql).fetchone()[0]


def test_no_reconciliation_failures(con):
    assert one(con, "SELECT count(*) FROM qa_reconciliation WHERE status = 'FAIL'") == 0


def test_every_warn_is_explained(con):
    assert one(con, "SELECT count(*) FROM qa_reconciliation WHERE status = 'WARN' AND coalesce(note, '') = ''") == 0


def test_eight_quarters_everywhere(con):
    for t in ["stg_plan_budgets", "stg_utilisation", "stg_sda_participants"]:
        assert one(con, f"SELECT count(DISTINCT report_date) FROM {t}") == 8, t


def test_masked_values_never_imputed_in_published_columns(con):
    assert one(con, "SELECT count(*) FROM stg_plan_budgets WHERE participants_masked AND participants IS NOT NULL") == 0
    assert one(con, "SELECT count(*) FROM stg_sda_participants WHERE participants_masked AND participants IS NOT NULL") == 0


def test_complementary_mask_fallback_is_flagged_and_bounded(con):
    assert one(con, """SELECT count(*) FROM stg_plan_budgets
                       WHERE participants IS NULL AND participants_filled IS NOT NULL
                         AND participants_source NOT LIKE 'district total%'""") == 0
    assert one(con, """SELECT count(*) FROM stg_plan_budgets
                       WHERE participants_source LIKE 'district total%' AND participants_filled > participants_upper""") == 0


def test_harmonised_age_band_covers_old_and_new(con):
    bands = {r[0] for r in con.execute(
        "SELECT DISTINCT age_band FROM stg_plan_budgets WHERE age_band_h = '0 to 14'").fetchall()}
    assert bands == {"0 to 6", "7 to 14", "0 to 8", "9 to 14"}


def test_sda_share_bounds_and_definition(con):
    assert one(con, "SELECT count(*) FROM mart_sda_district WHERE non_use_share < 0 OR non_use_share > 1") == 0
    assert one(con, """SELECT count(*) FROM mart_sda_district
                       WHERE non_use_share IS NOT NULL
                         AND abs(non_use_share - not_using / (in_use + not_using)) > 1e-9""") == 0
    assert one(con, """SELECT count(*) FROM mart_sda_district
                       WHERE non_use_share IS NOT NULL
                         AND (non_use_share < non_use_share_min - 1e-9 OR non_use_share > non_use_share_max + 1e-9)""") == 0


def test_sa4_context_never_joined_to_districts(con):
    # the SA4 mart has no district column: it cannot be joined on district by accident
    cols = {r[0] for r in con.execute("DESCRIBE mart_sda_sa4_context").fetchall()}
    assert "district" not in cols and "district_key" not in cols


def test_standardisation_identity(con):
    # raw gap = mix effect + like-for-like gap, by construction
    assert one(con, """SELECT max(abs(raw_gap - (mix_effect + like_for_like_gap)))
                       FROM mart_util_standardised""") < 1e-9


def test_shift_share_adds_up(con):
    assert one(con, """SELECT max(abs((rate_to - rate_from) - (mix_effect + rate_effect)))
                       FROM an_mix_decomposition""") < 1e-9


def test_shift_share_reconstructs_published_rate(con):
    # weighted reconstruction within 1.5 pts of the published (whole-percent) national rate
    assert one(con, "SELECT max(abs(reconstruction_error_to)) FROM an_mix_decomposition") < 0.015


def test_scenario_arithmetic(con):
    assert one(con, """SELECT max(abs(participants_moving_to_use
                                      - (baseline_not_using - scenario_share * denominator_eligible_total)))
                       FROM an_scenario_grid""") < 1e-9
    assert one(con, "SELECT count(*) FROM an_scenario_grid WHERE scenario_share < 0") == 0


def test_evidence_register_statuses_and_rules(con):
    statuses = {r[0] for r in con.execute("SELECT DISTINCT status FROM an_evidence_register").fetchall()}
    assert statuses == {"Observed pattern", "Requires investigation", "Unsupported conclusion"}
    assert one(con, "SELECT count(*) FROM an_evidence_register WHERE coalesce(rule, '') = ''") == 0
    # every investigation item says what extra information is needed
    assert one(con, """SELECT count(*) FROM an_evidence_register
                       WHERE status = 'Requires investigation' AND coalesce(information_needed, '') = ''""") == 0


def test_no_housing_shortage_claim_is_observed(con):
    assert one(con, """SELECT count(*) FROM an_evidence_register
                       WHERE lower(claim) LIKE '%shortage%' AND status <> 'Unsupported conclusion'""") == 0
