"""Generate the Power BI project (powerbi/NDIS_Funding_Access.pbip) over outputs/powerbi/*.csv.

Run after the pipeline; open the .pbip in Power BI Desktop and press Refresh. The model follows
DAX_measures.md: Power Query only sets types, and SA4 dwelling context is never related to districts.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT.parent / "tools"))
from pbip_writer import (Measure as M, Page, Table, Visual as V, build, col, footer, heading,  # noqa: E402
                         mea, where_in)

DATA = ROOT / "outputs" / "powerbi"
PCT, PTS, INT = "0.0%", "+0.0;-0.0;0.0", "#,0"

# ---------------------------------------------------------------- model
GRID_BASE = "FILTER ( an_scenario_grid, an_scenario_grid[reduction_pts] = 0 )"
tables = [
    Table("dim_period", "dim_period.csv", description="Reporting quarter; utilisation window is lagged 3 months"),
    Table("dim_district", "dim_district.csv", description="State x NDIS service district"),
    Table("fact_funding_util_district", "fact_funding_util_district.csv",
          formats={"utilisation": PCT, "committed_budget_est": "$#,0", "avg_annualised_budget": "$#,0"},
          description="Published rows incl. state/ALL aggregates: filter district_key before summing"),
    Table("fact_util_standardised", "fact_util_standardised.csv", measures=[
        M("Observed utilisation %", "AVERAGE ( fact_util_standardised[observed_rate] )", PCT, "Module A"),
        M("Expected utilisation %", "AVERAGE ( fact_util_standardised[expected_rate] )", PCT, "Module A"),
        M("National utilisation %", "AVERAGE ( fact_util_standardised[national_rate] )", PCT, "Module A"),
        M("Raw gap (pts)", "AVERAGE ( fact_util_standardised[raw_gap] ) * 100", PTS, "Module A"),
        M("Mix effect (pts)", "AVERAGE ( fact_util_standardised[mix_effect] ) * 100", PTS, "Module A"),
        M("Like-for-like gap (pts)", "AVERAGE ( fact_util_standardised[like_for_like_gap] ) * 100", PTS, "Module A"),
        M("Within-cell coverage %", "AVERAGE ( fact_util_standardised[within_cell_coverage] )", PCT, "Module A"),
    ]),
    Table("fact_util_cells", "fact_util_cells.csv", formats={"district_rate": PCT, "nat_rate": PCT}),
    Table("fact_util_national", "fact_util_national.csv", formats={"utilisation": PCT}, measures=[
        M("National utilisation by class %", "AVERAGE ( fact_util_national[utilisation] )", PCT, "Module A"),
        M("Latest national utilisation %", """
VAR d = CALCULATE ( MAX ( fact_util_national[report_date] ), ALL ( fact_util_national ) )
RETURN CALCULATE ( AVERAGE ( fact_util_national[utilisation] ), ALL ( fact_util_national ),
    fact_util_national[report_date] = d, fact_util_national[support_class] = "ALL" )""", PCT, "Module A"),
    ]),
    Table("fact_sda_district", "fact_sda_district.csv",
          formats={"non_use_share": PCT, "non_use_share_min": PCT, "non_use_share_max": PCT,
                   "national_non_use_share": PCT}, measures=[
        M("SDA in use", "SUM ( fact_sda_district[in_use] )", INT, "Module B"),
        M("SDA eligible not using", "SUM ( fact_sda_district[not_using] )", INT, "Module B"),
        M("SDA eligible total", "SUM ( fact_sda_district[eligible_total] )", INT, "Module B"),
        M("Non-use share %", "DIVIDE ( [SDA eligible not using], [SDA eligible total] )", PCT, "Module B"),
        M("National non-use share %", "AVERAGE ( fact_sda_district[national_non_use_share] )", PCT, "Module B"),
        M("Masked cells in view",
          "CALCULATE ( COUNTROWS ( fact_sda_district ), fact_sda_district[any_masked] = TRUE () ) + 0", INT, "Module B"),
        M("Latest national not using", """
VAR d = CALCULATE ( MAX ( fact_sda_district[report_date] ), ALL ( fact_sda_district ) )
RETURN CALCULATE ( MAX ( fact_sda_district[national_not_using] ), ALL ( fact_sda_district ),
    fact_sda_district[report_date] = d )""", INT, "Module B"),
        M("Latest national non-use share %", """
VAR d = CALCULATE ( MAX ( fact_sda_district[report_date] ), ALL ( fact_sda_district ) )
RETURN CALCULATE ( MAX ( fact_sda_district[national_non_use_share] ), ALL ( fact_sda_district ),
    fact_sda_district[report_date] = d )""", PCT, "Module B"),
    ]),
    Table("fact_sda_sa4_context", "fact_sda_sa4_context.csv", formats={"capacity_per_participant_with_need": "0.00"},
          description="SA4 regions, 30 Jun 2026; context only, not related to service districts"),
    Table("an_util_persistence", "an_util_persistence.csv",
          formats={c: PCT for c in ["mean_observed_rate", "latest_observed_rate", "mean_within_cell_coverage"]} |
          {c: "+0.000;-0.000;0.000" for c in ["mean_raw_gap", "mean_mix_effect", "mean_like_for_like_gap",
                                              "mean_within_cell_gap", "latest_like_for_like_gap"]}, measures=[
        M("Districts persistently low (raw)",
          "CALCULATE ( COUNTROWS ( an_util_persistence ), an_util_persistence[persistent_low_raw] = TRUE () ) + 0",
          INT, "Module A"),
        M("Districts persistently low (like-for-like)",
          "CALCULATE ( COUNTROWS ( an_util_persistence ), an_util_persistence[persistent_low_adjusted] = TRUE () ) + 0",
          INT, "Module A"),
        M("Low mostly explained by mix",
          "CALCULATE ( COUNTROWS ( an_util_persistence ), an_util_persistence[low_mostly_mix] = TRUE () ) + 0",
          INT, "Module A"),
        M("Mean like-for-like gap (pts)", "AVERAGE ( an_util_persistence[mean_like_for_like_gap] ) * 100", PTS,
          "Module A"),
    ]),
    Table("an_sda_persistence", "an_sda_persistence.csv",
          formats={c: PCT for c in ["mean_non_use_share", "latest_non_use_share", "latest_non_use_share_min",
                                    "latest_non_use_share_max"]} |
          {"mean_excess_pts": "+0.0;-0.0;0.0", "change_share_pts": "+0.0;-0.0;0.0",
           "latest_not_using_per_1000_active": "0.0"}, measures=[
        M("Districts persistently above national",
          "CALCULATE ( COUNTROWS ( an_sda_persistence ), an_sda_persistence[persistent_above_national] = TRUE () ) + 0",
          INT, "Module B"),
        M("Latest not using", "SUM ( an_sda_persistence[latest_not_using] )", INT, "Module B"),
    ]),
    Table("an_scenario_grid", "an_scenario_grid.csv", formats={"baseline_share": PCT, "scenario_share": PCT}),
    Table("Reduction pts", dax="SELECTCOLUMNS ( GENERATESERIES ( 0, 20, 1 ), \"reduction_pts\", [Value] )",
          columns={"reduction_pts": "int64"}, description="What-if: non-use share reduction in percentage points",
          measures=[
        M("Reduction pts value", "SELECTEDVALUE ( 'Reduction pts'[reduction_pts], 0 )", "0", "Scenario"),
        M("Baseline not using", f"SUMX ( {GRID_BASE}, an_scenario_grid[baseline_not_using] )", INT, "Scenario"),
        M("Baseline eligible", f"SUMX ( {GRID_BASE}, an_scenario_grid[denominator_eligible_total] )", INT,
          "Scenario"),
        M("Scenario participants moving to use", f"""
VAR pts = [Reduction pts value]
RETURN SUMX ( {GRID_BASE},
    VAR moved = an_scenario_grid[denominator_eligible_total] * pts / 100
    RETURN IF ( moved > an_scenario_grid[baseline_not_using], an_scenario_grid[baseline_not_using], moved ) )""", INT, "Scenario"),
        M("Scenario not using", "[Baseline not using] - [Scenario participants moving to use]", INT, "Scenario"),
        M("Scenario non-use share %", "DIVIDE ( [Scenario not using], [Baseline eligible] )", PCT, "Scenario"),
        M("Baseline non-use share %", "DIVIDE ( [Baseline not using], [Baseline eligible] )", PCT, "Scenario"),
        M("Scenario note", "\"Arithmetic only: each district's non-use share falls by \" & [Reduction pts value] & "
          "\" pts (floored at zero); eligible totals fixed at 30 Jun 2026; sum of published districts. Not a forecast.\"",
          "", "Scenario"),
    ]),
    Table("an_evidence_register", "an_evidence_register.csv", measures=[
        M("Observed patterns", "CALCULATE ( COUNTROWS ( an_evidence_register ), "
          "an_evidence_register[status] = \"Observed pattern\" ) + 0", INT, "Module C"),
        M("Requires investigation", "CALCULATE ( COUNTROWS ( an_evidence_register ), "
          "an_evidence_register[status] = \"Requires investigation\" ) + 0", INT, "Module C"),
        M("Unsupported conclusions", "CALCULATE ( COUNTROWS ( an_evidence_register ), "
          "an_evidence_register[status] = \"Unsupported conclusion\" ) + 0", INT, "Module C"),
    ]),
    Table("an_sensitivity_flags", "an_sensitivity_flags.csv"),
    Table("an_sensitivity_overlap", "an_sensitivity_overlap.csv", formats={"jaccard": "0.00"}),
    Table("an_sensitivity_threshold", "an_sensitivity_threshold.csv"),
    Table("an_mix_decomposition", "an_mix_decomposition.csv",
          formats={c: PCT for c in ["rate_from", "rate_to", "published_from", "published_to"]} |
          {c: "+0.0000;-0.0000;0" for c in ["mix_effect", "rate_effect", "reconstruction_error_to"]}),
    Table("qa_reconciliation", "qa_reconciliation.csv", measures=[
        M("Checks passed", "CALCULATE ( COUNTROWS ( qa_reconciliation ), qa_reconciliation[status] = \"PASS\" ) + 0",
          INT, "QA"),
        M("Checks warned", "CALCULATE ( COUNTROWS ( qa_reconciliation ), qa_reconciliation[status] = \"WARN\" ) + 0",
          INT, "QA"),
        M("Checks failed", "CALCULATE ( COUNTROWS ( qa_reconciliation ), qa_reconciliation[status] = \"FAIL\" ) + 0",
          INT, "QA"),
    ]),
]

relationships = [f"{t}.report_date dim_period.report_date".split() for t in
                 ["fact_funding_util_district", "fact_util_standardised", "fact_util_cells", "fact_util_national",
                  "fact_sda_district"]]
relationships += [f"{t}.district_key dim_district.district_key".split() for t in
                  ["fact_funding_util_district", "fact_util_standardised", "fact_util_cells", "fact_sda_district",
                   "an_util_persistence", "an_sda_persistence", "an_scenario_grid"]]

# ---------------------------------------------------------------- report
UP, SDA, ST, EV, GR = "an_util_persistence", "an_sda_persistence", "fact_util_standardised", "an_evidence_register", \
    "an_scenario_grid"
state_slicer = V("slicer", 1060, 2, 200, 84, "State", {"Values": [col("dim_district", "state")]})
UTIL_NOTE = ("Utilisation = payments / committed supports over the 6 months ending 3 months before each reporting "
             "date (NDIA rule). Published as whole %, so gaps under 1 pt are within rounding.")
SDA_NOTE = ("SDA dwelling counts are not vacancies, and SA4 dwelling data are never joined to service districts. "
            "Masked cells (<11) are shown at their published bound.")


def cards(y, items):
    """A row of cards; the label under each value is the card's caption."""
    w = (1240 - 20 * (len(items) - 1)) // len(items)
    return [V("card", 20 + i * (w + 20), y, w, 96, fields={"Values": [(mea(t, m), label)]})
            for i, (t, m, label) in enumerate(items)]


def cols(t, labels):
    """Table columns with readable headers: {column: header}."""
    return [(col(t, c), h) for c, h in labels.items()]


pages = [
    Page("Funding-Utilisation", [
        heading("Funding-Utilisation Explorer",
                "Is low utilisation persistent, and does it survive a like-for-like (disability × age mix) "
                "comparison?", 1020),
        state_slicer,
        *cards(80, [("fact_util_national", "Latest national utilisation %", "National utilisation, Jun-26"),
                    (UP, "Districts persistently low (raw)", "Persistently low (raw)"),
                    (UP, "Districts persistently low (like-for-like)", "Persistently low (like-for-like)"),
                    (UP, "Low mostly explained by mix", "Low mainly from mix")]),
        V("line", 20, 190, 600, 290, "National utilisation by support class",
          {"Category": [col("fact_util_national", "report_date")],
           "Series": [col("fact_util_national", "support_class")],
           "Y": [mea("fact_util_national", "National utilisation by class %")]},
          filters=[(col("fact_util_national", "support_class"),
                    where_in(col("fact_util_national", "support_class"), ["SuppClass_Missing"], negate=True))]),
        V("bar", 640, 190, 620, 290, "Persistently low after adjustment: mean like-for-like gap (pts)",
          {"Category": [col(UP, "district_key")], "Y": [mea(UP, "Mean like-for-like gap (pts)")]},
          sort=[(mea(UP, "Mean like-for-like gap (pts)"), "Ascending")],
          filters=[(col(UP, "persistent_low_adjusted"), where_in(col(UP, "persistent_low_adjusted"), [True]))]),
        V("table", 20, 490, 1240, 190, "District persistence (8 quarters)",
          {"Values": cols(UP, {"state": "State", "district": "District",
                               "quarters_bottom_quintile_raw": "Qtrs bottom fifth (raw)",
                               "quarters_bottom_quintile_adjusted": "Qtrs bottom fifth (like-for-like)",
                               "latest_observed_rate": "Utilisation Jun-26", "mean_mix_effect": "Mean mix effect",
                               "mean_like_for_like_gap": "Mean like-for-like gap",
                               "persistent_low_adjusted": "Persistent (like-for-like)",
                               "low_mostly_mix": "Mostly mix"})},
          sort=[(col(UP, "quarters_bottom_quintile_adjusted"), "Descending")]),
        footer(UTIL_NOTE),
    ]),
    Page("District like-for-like", [
        heading("District like-for-like trend",
                "Observed rate vs the rate expected from the district's own participant mix at national rates.",
                1020),
        V("slicer", 960, 2, 300, 84, "District", {"Values": [col("dim_district", "district_key")]},
          single=True, default=["NT | East Arnhem"]),
        V("line", 20, 80, 760, 400, "Observed vs expected vs national",
          {"Category": [col("dim_period", "report_date")],
           "Y": [mea(ST, "Observed utilisation %"), mea(ST, "Expected utilisation %"),
                 mea(ST, "National utilisation %")]}),
        *[V("card", 800, 80 + i * 100, 460, 90, fields={"Values": [(mea(ST, m), t)]}) for i, (m, t) in enumerate(
            [("Raw gap (pts)", "Raw gap to national (pts, all quarters)"),
             ("Mix effect (pts)", "Explained by participant mix (pts)"),
             ("Like-for-like gap (pts)", "Like-for-like gap (pts)"),
             ("Within-cell coverage %", "Budget share in unmasked cells")])],
        V("table", 20, 490, 1240, 190, "By quarter",
          {"Values": [(col("dim_period", "report_date"), "Reporting date"),
                      (col("dim_period", "util_window_end"), "Utilisation window ends"),
                      mea(ST, "Observed utilisation %"), mea(ST, "Expected utilisation %"),
                      mea(ST, "National utilisation %"), mea(ST, "Mix effect (pts)"),
                      mea(ST, "Like-for-like gap (pts)")]}),
        footer(UTIL_NOTE + " Mix effect = expected − national; like-for-like gap = observed − expected."),
    ]),
    Page("SDA Access Gap", [
        heading("SDA Access Gap Monitor",
                "Participants with an SDA budget who are not living in SDA: how many, what share, and does it "
                "persist?", 1020),
        state_slicer,
        *cards(80, [("fact_sda_district", "Latest national not using", "Eligible, not using SDA, Jun-26"),
                    ("fact_sda_district", "Latest national non-use share %", "National non-use share, Jun-26"),
                    (SDA, "Districts persistently above national", "Persistently above national"),
                    ("fact_sda_district", "Masked cells in view", "Masked district-quarters")]),
        V("line", 20, 190, 600, 290, "Non-use share: selection vs national",
          {"Category": [col("dim_period", "report_date")],
           "Y": [mea("fact_sda_district", "Non-use share %"), mea("fact_sda_district", "National non-use share %")]}),
        V("bar", 640, 190, 620, 290, "Persistently above national: eligible not using, Jun-26",
          {"Category": [col(SDA, "district_key")], "Y": [mea(SDA, "Latest not using")]},
          sort=[(mea(SDA, "Latest not using"), "Descending")],
          filters=[(col(SDA, "persistent_above_national"), where_in(col(SDA, "persistent_above_national"), [True]))]),
        V("table", 20, 490, 1240, 190, "District persistence (8 quarters)",
          {"Values": cols(SDA, {"state": "State", "district": "District",
                                "quarters_above_national": "Qtrs above national",
                                "latest_not_using": "Not using Jun-26", "latest_eligible_total": "Eligible Jun-26",
                                "latest_non_use_share": "Non-use share Jun-26",
                                "mean_excess_pts": "Mean excess (pts)", "change_share_pts": "Change (pts)",
                                "latest_any_masked": "Masked", "persistent_above_national": "Persistent"})},
          sort=[(col(SDA, "quarters_above_national"), "Descending")]),
        footer(SDA_NOTE),
    ]),
    Page("Sensitivity", [
        heading("Sensitivity: do the flagged districts depend on the definition?",
                "Five definitions per module. Robust = flagged by at least 3 of 5. Count-based and share-based "
                "rankings point at different districts."),
        V("table", 20, 80, 760, 600, "Districts by number of definitions flagging them",
          {"Values": cols("an_sensitivity_flags", {"module": "Module", "district_key": "District",
                                                   "definitions_flagging": "Definitions flagging (of 5)",
                                                   "robustness": "Robustness", "definitions": "Definitions"})},
          sort=[(col("an_sensitivity_flags", "definitions_flagging"), "Descending")]),
        V("table", 800, 80, 460, 300, "Overlap between definitions (Jaccard)",
          {"Values": cols("an_sensitivity_overlap", {"module": "Module", "definition_a": "Definition A",
                                                     "definition_b": "Definition B", "shared": "Shared",
                                                     "jaccard": "Jaccard"})},
          filters=[(col("an_sensitivity_overlap", "jaccard"),
                    where_in(col("an_sensitivity_overlap", "jaccard"), [1.0], negate=True))]),
        V("table", 800, 390, 460, 290, "SDA top 10 vs minimum eligible threshold",
          {"Values": cols("an_sensitivity_threshold", {"min_eligible": "Min eligible",
                                                       "districts_ranked": "Districts ranked",
                                                       "overlap_with_base": "Overlap with base top 10",
                                                       "top10": "Top 10"})}),
    ]),
    Page("Evidence register", [
        heading("Evidence register",
                "Every claim is classified: Observed pattern / Requires investigation / Unsupported conclusion.",
                800),
        V("slicer", 840, 2, 200, 84, "Module", {"Values": [col(EV, "module")]}),
        V("slicer", 1060, 2, 200, 84, "Status", {"Values": [col(EV, "status")]}),
        *cards(80, [(EV, "Observed patterns", "Observed pattern"),
                    (EV, "Requires investigation", "Requires investigation"),
                    (EV, "Unsupported conclusions", "Unsupported conclusion")]),
        V("table", 20, 190, 1240, 490, "Claims",
          {"Values": cols(EV, {"claim_id": "ID", "module": "Module", "scope": "Scope", "topic": "Topic",
                               "claim": "Claim", "status": "Status", "rule": "Rule", "caveats": "Caveats",
                               "information_needed": "Information needed"})},
          sort=[(col(EV, "claim_id"), "Ascending")]),
    ]),
    Page("Scenario explorer", [
        heading("Scenario explorer: what if non-use fell?",
                "Pick a reduction in each district's non-use share (percentage points). Arithmetic only.", 960),
        V("slicer", 1000, 2, 260, 84, "Reduction (pts)", {"Values": [col("Reduction pts", "reduction_pts")]},
          single=True, default=[5]),
        *cards(80, [("Reduction pts", "Baseline non-use share %", "Baseline non-use share"),
                    ("Reduction pts", "Scenario non-use share %", "Scenario non-use share"),
                    ("Reduction pts", "Scenario participants moving to use", "Participants moving into SDA"),
                    ("Reduction pts", "Scenario not using", "Still not using")]),
        V("card", 20, 186, 1240, 50, fields={"Values": [mea("Reduction pts", "Scenario note")]}, font=11),
        V("table", 20, 246, 1240, 434, "By district (denominator: eligible total at 30 Jun 2026)",
          {"Values": [(col(GR, "district_key"), "District"), mea("Reduction pts", "Baseline eligible"),
                      mea("Reduction pts", "Baseline not using"), mea("Reduction pts", "Baseline non-use share %"),
                      mea("Reduction pts", "Scenario participants moving to use"),
                      mea("Reduction pts", "Scenario not using"), mea("Reduction pts", "Scenario non-use share %")]},
          sort=[(mea("Reduction pts", "Baseline not using"), "Descending")], totals=True),
    ]),
    Page("Data quality", [
        heading("Reconciliation to NDIA",
                "Load counts, state and district totals, masking bounds and cross-source checks (rerun every "
                "pipeline run).", 1020),
        V("slicer", 1060, 2, 200, 84, "Check kind", {"Values": [col("qa_reconciliation", "kind")]}),
        *cards(80, [("qa_reconciliation", "Checks passed", "PASS"), ("qa_reconciliation", "Checks warned", "WARN"),
                    ("qa_reconciliation", "Checks failed", "FAIL")]),
        V("table", 20, 190, 1240, 490, "Checks",
          {"Values": cols("qa_reconciliation", {"check_id": "Check", "kind": "Kind", "description": "Description",
                                                "expected": "Expected", "actual": "Actual",
                                                "difference": "Difference", "tolerance": "Tolerance",
                                                "status": "Status", "note": "Note"})},
          sort=[(col("qa_reconciliation", "status"), "Descending")]),
    ]),
]

if __name__ == "__main__":
    print(build(ROOT / "powerbi", "NDIS_Funding_Access", DATA, tables, relationships, pages))
