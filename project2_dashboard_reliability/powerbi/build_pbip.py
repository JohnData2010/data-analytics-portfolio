"""Generate the Power BI project (powerbi/KPI_Reliability_Lab.pbip) over outputs/powerbi/*.csv.

Run after the pipeline; open the .pbip in Power BI Desktop and press Refresh. Follows
PowerBI_build_guide.md: datasets A and B are never related to each other; one dataset slicer
(dim_dataset) filters every summary table, and every median card carries its reliability note.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT.parent / "tools"))
from pbip_writer import (Measure as M, Page, Table, Visual as V, build, col, footer, heading,  # noqa: E402
                         mea)

DATA = ROOT / "outputs" / "powerbi"
PCT, DAYS, INT = "0.0%", "0.0", "#,0"
ONE = "IF ( HASONEVALUE ( dim_dataset[dataset] ), {}, BLANK () )"   # guard: KPIs never mix A and B


def all_groups(expr):
    return ONE.format(f"CALCULATE ( {expr}, kpi_summary[service_group] = \"(all)\" )")


def kpi_value(kpi, column="value"):
    return ONE.format(f"CALCULATE ( MAX ( kpi_meaningfulness[{column}] ), kpi_meaningfulness[kpi] = \"{kpi}\" )")


# ---------------------------------------------------------------- model
tables = [
    Table("dim_dataset", dax='DATATABLE ( "dataset", STRING, "dataset_label", STRING, { '
          '{ "A", "A: resolution time, Nov 2014 to Nov 2016" }, '
          '{ "B", "B: digital channel, Jan to Sep 2018 (Salesforce subset)" } } )',
          columns={"dataset": "string", "dataset_label": "string"},
          description="The two City of Melbourne extracts; never merged"),
    Table("kpi_summary", "kpi_summary.csv",
          formats={"k01_median_days": DAYS, "k02_p90_days": DAYS, "k04_open_median_age": DAYS,
                   "k04_open_share_over_90d": PCT, "k05_open_share": PCT, "requests": INT, "closed": INT,
                   "open": INT}, measures=[
        M("Total requests", all_groups("SUM ( kpi_summary[requests] )"), INT, "Service performance"),
        M("Open requests", all_groups("SUM ( kpi_summary[open] )"), INT, "Service performance"),
        M("Open share", all_groups("MAX ( kpi_summary[k05_open_share] )"), PCT, "Service performance"),
        M("Median days (closed only)", all_groups("MAX ( kpi_summary[k01_median_days] )"), DAYS,
          "Service performance"),
        M("P90 days (closed only)", all_groups("MAX ( kpi_summary[k02_p90_days] )"), DAYS, "Service performance"),
        M("Open median age (days)", all_groups("MAX ( kpi_summary[k04_open_median_age] )"), DAYS,
          "Service performance"),
    ]),
    Table("kpi_meaningfulness", "kpi_meaningfulness.csv", formats={"value": "0.0##"}, measures=[
        M("KM median days (open = censored)", kpi_value("K06"), DAYS, "Service performance"),
        M("Median reliability note", ONE.format(
            '"Closed-only median is " & CALCULATE ( MAX ( kpi_meaningfulness[meaningful] ), '
            'kpi_meaningfulness[kpi] = "K01" ) & ": " & CALCULATE ( MAX ( kpi_meaningfulness[reason] ), '
            'kpi_meaningfulness[kpi] = "K01" )'), "", "Service performance"),
    ]),
    Table("kpi_cohort", "kpi_cohort.csv", formats={"k03_closure_30d": PCT}),
    Table("stress_kpi_values", "stress_kpi_values.csv", formats={"rank": "0"}, measures=[
        M("KPI value", "AVERAGE ( stress_kpi_values[value] )", "0.0##", "Stress test"),
        M("KPI rank", "MIN ( stress_kpi_values[rank] )", "0", "Stress test"),
    ]),
    Table("stress_reversals", "stress_reversals.csv"),
    Table("stress_rank_agreement", "stress_rank_agreement.csv", formats={"spearman": "0.00"}),
    Table("km_curves", "km_curves.csv", formats={"share_closed": PCT, "survival": PCT}, measures=[
        M("Share closed", "IF ( MAX ( km_curves[t] ) <= 365, AVERAGE ( km_curves[share_closed] ) )", PCT,
          "Survival"),
    ]),
    Table("sensitivity_scenarios", "sensitivity_scenarios.csv",
          formats={"K03": PCT, "K05": PCT, "d_K03": "+0.0%;-0.0%;0.0%", "d_K01": "+0.0;-0.0;0.0",
                   "d_K06": "+0.0;-0.0;0.0", "K01": DAYS, "K02": DAYS, "K06": DAYS}),
    Table("sensitivity_trend", "sensitivity_trend.csv"),
    Table("kpi_integrity_recompute", "kpi_integrity_recompute.csv"),
    Table("qa_dq_results", "qa_dq_results.csv", formats={"rate": "0.00%"}, measures=[
        M("DQ checks passed", "CALCULATE ( COUNTROWS ( qa_dq_results ), qa_dq_results[status] = \"PASS\" ) + 0",
          INT, "Data quality"),
        M("DQ checks warned", "CALCULATE ( COUNTROWS ( qa_dq_results ), qa_dq_results[status] = \"WARN\" ) + 0",
          INT, "Data quality"),
        M("DQ checks failed", "CALCULATE ( COUNTROWS ( qa_dq_results ), qa_dq_results[status] = \"FAIL\" ) + 0",
          INT, "Data quality"),
    ]),
    Table("qa_row_lineage", "qa_row_lineage.csv", formats={"rows": INT}),
    Table("gov_source_manifest", "gov_source_manifest.csv", formats={"rows": INT, "portal_records_count": INT}),
    Table("coverage_assessment", "coverage_assessment.csv",
          description="Not related to dim_dataset: some findings span A+B"),
    Table("backlog_reconstruction", "backlog_reconstruction.csv", measures=[
        M("Reconstructed open requests", "SUM ( backlog_reconstruction[reconstructed_open] )", INT, "Coverage"),
    ]),
    Table("backlog_limitations", "backlog_limitations.csv"),
    Table("kpi_period_comparability", "kpi_period_comparability.csv"),
    Table("fact_requests_a", "fact_requests_a.csv", description="Dataset A, one row per request (2014-16)"),
    Table("fact_requests_b", "fact_requests_b.csv",
          description="Dataset B, one row per request (2018 Salesforce subset); local-time dates"),
]

relationships = [(f"{t}.dataset", "dim_dataset.dataset") for t in
                 ["kpi_summary", "kpi_meaningfulness", "kpi_cohort", "stress_kpi_values", "stress_reversals",
                  "stress_rank_agreement", "km_curves", "sensitivity_scenarios", "sensitivity_trend",
                  "kpi_integrity_recompute", "qa_dq_results", "qa_row_lineage", "gov_source_manifest",
                  "backlog_reconstruction"]]

# ---------------------------------------------------------------- report
SUBSET = ("Dataset B is a subset of council requests (Salesforce only) and is never merged with A. "
          "Latest record: A 08-Nov-2016, B 17-Oct-2018.")


def dataset_slicer(x=1000, w=260):
    return V("slicer", x, 2, w, 84, "Dataset", {"Values": [col("dim_dataset", "dataset_label")]}, single=True,
             default=["B: digital channel, Jan to Sep 2018 (Salesforce subset)"])


def cards(y, items):
    w = (1240 - 20 * (len(items) - 1)) // len(items)
    return [V("card", 20 + i * (w + 20), y, w, 96, fields={"Values": [(mea(t, m), label)]})
            for i, (t, m, label) in enumerate(items)]


def cols(t, labels):
    return [(col(t, c), h) for c, h in labels.items()]


pages = [
    Page("Service performance", [
        heading("Service performance",
                "The headline KPIs, each next to the number that shows what the headline hides.", 960),
        dataset_slicer(),
        *cards(92, [("kpi_summary", "Total requests", "Requests"),
                    ("kpi_summary", "Median days (closed only)", "Median days, closed only"),
                    ("kpi_meaningfulness", "KM median days (open = censored)", "Median days, open counted"),
                    ("kpi_summary", "Open share", "Still open at extract"),
                    ("kpi_summary", "Open median age (days)", "Median age of open (days)")]),
        V("card", 20, 196, 1240, 50, fields={"Values": [mea("kpi_meaningfulness", "Median reliability note")]},
          font=11),
        V("table", 20, 256, 1240, 424, "By service group",
          {"Values": cols("kpi_summary", {"service_group": "Service group", "requests": "Requests",
                                          "closed": "Closed", "open": "Open",
                                          "k01_median_days": "Median days (closed)",
                                          "k02_p90_days": "P90 days (closed)",
                                          "k04_open_median_age": "Open median age",
                                          "k04_open_share_over_90d": "Open >90 days",
                                          "k05_open_share": "Open share"})},
          sort=[(col("kpi_summary", "requests"), "Descending")]),
        footer(SUBSET),
    ]),
    Page("KPI stress test", [
        heading("KPI stress test: does the ranking depend on the KPI?",
                "Same service groups, six KPIs. Pick a KPI: the 'fastest' group on the median can be the slowest "
                "on p90.", 700),
        V("slicer", 740, 2, 240, 84, "KPI", {"Values": [col("stress_kpi_values", "kpi_label")]}, single=True,
          default=["P90 days (completed)"]),
        dataset_slicer(),
        V("bar", 20, 92, 620, 588, "Selected KPI by service group (rank in labels table)",
          {"Category": [col("stress_kpi_values", "service_group")], "Y": [mea("stress_kpi_values", "KPI value")]},
          sort=[(mea("stress_kpi_values", "KPI value"), "Descending")]),
        V("table", 660, 92, 600, 290, "Ranking reversals vs the closed-only median",
          {"Values": cols("stress_reversals", {"faster_on_median": "Faster on median",
                                               "slower_on_median": "Slower on median", "kpi_label": "But on",
                                               "faster_value": "Value (faster)", "slower_value": "Value (slower)"})}),
        V("table", 660, 392, 600, 288, "Rank agreement between KPIs (Spearman)",
          {"Values": cols("stress_rank_agreement", {"kpi_a": "KPI A", "kpi_b": "KPI B", "spearman": "Spearman",
                                                    "groups": "Groups"})},
          sort=[(col("stress_rank_agreement", "spearman"), "Ascending")]),
        footer(SUBSET),
    ]),
    Page("Survival", [
        heading("Time to close, counting open requests",
                "Kaplan-Meier: share of requests closed by day t, with open requests treated as censored "
                "(first 365 days).", 700),
        V("slicer", 740, 2, 240, 84, "Service group", {"Values": [col("km_curves", "service_group")]},
          default=["(all)"]),
        dataset_slicer(),
        V("line", 20, 92, 960, 588, "Share closed by day",
          {"Category": [(col("km_curves", "t"), "Days since received")],
           "Series": [col("km_curves", "service_group")], "Y": [mea("km_curves", "Share closed")]}),
        V("card", 1000, 92, 260, 120, fields={"Values": [(mea("kpi_meaningfulness",
                                                              "KM median days (open = censored)"),
                                                          "Median days, open counted")]}),
        V("card", 1000, 222, 260, 120, fields={"Values": [(mea("kpi_summary", "Median days (closed only)"),
                                                          "Median days, closed only")]}),
        V("card", 1000, 352, 260, 120, fields={"Values": [(mea("kpi_summary", "Open share"), "Still open")]}),
        footer(SUBSET),
    ]),
    Page("Sensitivity", [
        heading("Sensitivity: which assumptions move the KPIs?",
                "Each scenario changes one analyst choice. Watch whether the slowest group changes.", 960),
        dataset_slicer(),
        V("table", 20, 92, 1240, 380, "Scenarios",
          {"Values": cols("sensitivity_scenarios", {
              "scenario": "Scenario", "assumption_type": "Assumption", "requests": "Requests",
              "K01": "Median (closed)", "K06": "KM median", "K03": "Closed ≤30d", "d_K01": "Δ median",
              "d_K06": "Δ KM median", "slowest_by_median": "Slowest (median)", "slowest_by_km": "Slowest (KM)",
              "slowest_changed": "Slowest changed", "median_vs_km_disagree": "Median vs KM disagree"})}),
        V("table", 20, 482, 1240, 198, "Trend direction by KPI",
          {"Values": cols("sensitivity_trend", {"kpi_label": "KPI", "period_from": "From", "period_to": "To",
                                                "value_from": "Value from", "value_to": "Value to",
                                                "change": "Change", "direction": "Direction"})}),
        footer(SUBSET),
    ]),
    Page("Data quality & lineage", [
        heading("Data quality & lineage",
                "Every rule reports rows flagged and what each KPI would become if they were excluded.", 960),
        dataset_slicer(),
        *cards(92, [("qa_dq_results", "DQ checks passed", "PASS"), ("qa_dq_results", "DQ checks warned", "WARN"),
                    ("qa_dq_results", "DQ checks failed", "FAIL")]),
        V("table", 20, 198, 1240, 300, "Data quality rules",
          {"Values": cols("qa_dq_results", {"rule_id": "Rule", "check": "Check", "severity": "Severity",
                                            "rows_flagged": "Rows flagged", "rate": "Rate",
                                            "kpi_impact_if_excluded": "KPI impact if excluded",
                                            "status": "Status"})},
          sort=[(col("qa_dq_results", "rule_id"), "Ascending")]),
        V("table", 20, 508, 400, 172, "Row counts through the pipeline",
          {"Values": cols("qa_row_lineage", {"step": "Step", "rows": "Rows"})}),
        V("table", 440, 508, 820, 172, "Source files",
          {"Values": cols("gov_source_manifest", {"file": "File", "rows": "Rows",
                                                  "portal_records_count": "Portal count",
                                                  "portal_modified": "Portal modified", "sha256": "SHA-256"})}),
        footer(SUBSET),
    ]),
    Page("Coverage & limits", [
        heading("Coverage & limits: what the data cannot show",
                "Backlog is reconstructed from received and closed dates; early months miss requests opened "
                "before the extract began.", 960),
        dataset_slicer(),
        V("line", 20, 92, 620, 330, "Reconstructed open requests at month end",
          {"Category": [(col("backlog_reconstruction", "month_end"), "Month end")],
           "Series": [(col("backlog_reconstruction", "reliable"), "Reliable")],
           "Y": [mea("backlog_reconstruction", "Reconstructed open requests")]}),
        V("table", 660, 92, 600, 330, "Why the backlog is a lower bound",
          {"Values": cols("backlog_limitations", {"limitation": "Limitation", "detail": "Detail"})}),
        V("table", 20, 432, 820, 248, "Coverage assessment (A, B and A+B)",
          {"Values": cols("coverage_assessment", {"dataset": "Dataset", "aspect": "Aspect", "finding": "Finding",
                                                  "implication": "Implication"})}),
        V("table", 860, 432, 400, 248, "Can each KPI be compared across periods?",
          {"Values": cols("kpi_period_comparability", {"kpi": "KPI",
                                                       "comparability_across_periods": "Comparability"})}),
        footer(SUBSET),
    ]),
]

if __name__ == "__main__":
    print(build(ROOT / "powerbi", "KPI_Reliability_Lab", DATA, tables, relationships, pages))
