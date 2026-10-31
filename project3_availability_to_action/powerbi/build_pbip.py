"""Generate the Power BI project (powerbi/Availability_to_Action.pbip) over outputs/powerbi/*.csv.

Run after the pipeline; open the .pbip in Power BI Desktop and press Refresh. Power Query only sets types: every
number is computed by the pipeline. dim_store -> dim_series -> facts is a snowflake with single-direction filters;
evidence tables (an_*, qa_*) are not related to anything, so slicers never silently re-cut a backtest result.
"""
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT.parent / "tools"))
from pbip_writer import (Measure as M, Page, Table, Visual as V, build, col, footer, heading,  # noqa: E402
                         mea, where_in)

DATA = ROOT / "outputs" / "powerbi"
PCT, NUM, INT = "0.0%", "#,0.0", "#,0"
LISTED = "decision_list[in_store_top_n] = TRUE ()"

# ---------------------------------------------------------------- model
tables = [
    Table("dim_store", "dim_store.csv", description="898 stores (encoded ids)"),
    Table("dim_series", "dim_series.csv", description="One store-product series; product hierarchy"),
    Table("dim_date", "dim_date.csv", description="The 28 history days before the holdout decision + the outcome week"),
    Table("decision_list", "decision_list.csv", description="Holdout-week review list: top 25 per store, scored on day 91",
          formats={"expected_lost_7d": NUM, "imputed_share": PCT, "hidden_vs_recorded": PCT, "obs_28": NUM,
                   "rec_28": NUM, "lost_28": NUM, "outcome_lost": NUM}, measures=[
        M("Listed series", f"CALCULATE ( COUNTROWS ( decision_list ), {LISTED} )", INT, "Review list"),
        M("Expected lost demand, listed (7 days)", f"CALCULATE ( SUM ( decision_list[expected_lost_7d] ), {LISTED} )",
          NUM, "Review list"),
        M("Realised lost demand, listed", f"CALCULATE ( SUM ( decision_list[outcome_lost] ), {LISTED} )", NUM,
          "Review list"),
        M("Realised vs expected", "DIVIDE ( [Realised lost demand, listed], [Expected lost demand, listed (7 days)] )",
          PCT, "Review list"),
        M("Low-confidence listed", f"CALCULATE ( COUNTROWS ( decision_list ), {LISTED}, "
          "decision_list[confidence] = \"Low\" ) + 0", INT, "Review list"),
    ]),
    Table("fact_series_day", "fact_series_day.csv", description="Listed series only, history + outcome week",
          measures=[
        M("Recorded sales", "SUM ( fact_series_day[recorded] )", NUM, "Series"),
        M("Recovered demand", "SUM ( fact_series_day[recovered] )", NUM, "Series"),
        M("Stockout hours", "SUM ( fact_series_day[oos_hours] )", INT, "Series"),
    ]),
    Table("fact_store_day", "fact_store_day.csv", measures=[
        M("Store recorded sales", "SUM ( fact_store_day[recorded] )", NUM, "Store"),
        M("Store recovered demand", "SUM ( fact_store_day[recovered] )", NUM, "Store"),
        M("Hidden demand vs recorded", "DIVIDE ( SUM ( fact_store_day[lost_partial] ), SUM ( fact_store_day[recorded] ) )",
          PCT, "Store"),
        M("Series-days with a stockout", "DIVIDE ( SUM ( fact_store_day[series_days_with_stockout] ), "
          "SUM ( fact_store_day[series] ) )", PCT, "Store"),
        M("Unavailable series-days", "SUM ( fact_store_day[unavailable_series_days] )", INT, "Store"),
    ]),
    Table("an_method_summary", "an_method_summary.csv", formats={c: PCT for c in [
        "store_top10_lost_capture", "worst_fold_lost_capture", "store_top10_hours_capture",
        "global_top10pct_lost_capture", "store_top10_unavailable_capture"]}),
    Table("an_eval_fold", "an_eval_fold.csv", measures=[
        M("Lost demand captured (store top 10)", "AVERAGE ( an_eval_fold[store_top10_lost_capture] )", PCT, "Backtest"),
        M("Stockout hours captured (store top 10)", "AVERAGE ( an_eval_fold[store_top10_hours_capture] )", PCT,
          "Backtest"),
    ]),
    Table("an_bootstrap", "an_bootstrap.csv", formats={"difference": "+0.00%;-0.00%", "ci_low": "+0.00%;-0.00%",
                                                       "ci_high": "+0.00%;-0.00%"}),
    Table("an_variant_selection", "an_variant_selection.csv", formats={"dev_mean": "0.00%"}),
    Table("an_masking_summary", "an_masking_summary.csv", formats={"wape": PCT, "bias": "+0.0%;-0.0%"}, measures=[
        M("Masking error (WAPE)", "AVERAGE ( an_masking_summary[wape] )", PCT, "Recovery"),
    ]),
    Table("an_forecast_check", "an_forecast_check.csv", formats={"wape": PCT, "bias": "+0.0%;-0.0%"}),
    Table("an_confidence_check", "an_confidence_check.csv",
          formats={"realised_over_expected_listed": PCT, "listed_with_stockout_next_week": PCT, "masking_wape": PCT}),
    Table("an_hour_profile", "an_hour_profile.csv", measures=[
        M("Flagged out of stock", "AVERAGE ( an_hour_profile[oos_share] )", PCT, "Data quality"),
        M("Share of daily sales", "AVERAGE ( an_hour_profile[sales_share] )", PCT, "Data quality"),
    ]),
    Table("qa_dq_results", "qa_dq_results.csv", formats={"rate": "0.00%"}, measures=[
        M("DQ checks passed", "CALCULATE ( COUNTROWS ( qa_dq_results ), qa_dq_results[status] = \"PASS\" ) + 0",
          INT, "Data quality"),
        M("DQ checks warned", "CALCULATE ( COUNTROWS ( qa_dq_results ), qa_dq_results[status] = \"WARN\" ) + 0",
          INT, "Data quality"),
        M("DQ checks failed", "CALCULATE ( COUNTROWS ( qa_dq_results ), qa_dq_results[status] = \"FAIL\" ) + 0",
          INT, "Data quality"),
    ]),
    Table("qa_row_lineage", "qa_row_lineage.csv", formats={"rows": INT}),
    Table("gov_source_manifest", "gov_source_manifest.csv", formats={"rows": INT, "bytes": INT}),
]

# encoded ids and ranks are read as decimals; show them as whole numbers
for t in tables:
    if t.csv:
        for c in pd.read_csv(DATA / t.csv, nrows=0).columns:
            if c.endswith("_id") or c in ("store_rank", "global_rank", "step_no", "fold"):
                t.formats.setdefault(c, "0")

relationships = [
    ("dim_series.store_id", "dim_store.store_id"),
    ("decision_list.series_id", "dim_series.series_id"),
    ("fact_series_day.series_id", "dim_series.series_id"),
    ("fact_series_day.dt", "dim_date.dt"),
    ("fact_store_day.store_id", "dim_store.store_id"),
    ("fact_store_day.dt", "dim_date.dt"),
]

# ---------------------------------------------------------------- report
NOTE = ("FreshRetailNet-50K (Dingdong Inc., CC BY 4.0). Units are normalised sales, not currency. Decision day = "
        "26-Jun-2024; history 28-May to 25-Jun; outcome week 26-Jun to 02-Jul-2024.")


# open on the store holding the single highest-scored series (store ids are read as decimals by the model)
DEFAULT_STORE = float(pd.read_csv(DATA / "decision_list.csv").query("global_rank == 1")["store_id"].iat[0])


def store_slicer(x=1000, w=260):
    return V("slicer", x, 2, w, 84, "Store", {"Values": [col("dim_store", "store_id")]}, single=True,
             default=[DEFAULT_STORE])


def cards(y, items):
    w = (1240 - 20 * (len(items) - 1)) // len(items)
    return [V("card", 20 + i * (w + 20), y, w, 96, fields={"Values": [(mea(t, m), label)]})
            for i, (t, m, label) in enumerate(items)]


def cols(t, labels):
    return [(col(t, c), h) for c, h in labels.items()]


TOP10 = [(col("decision_list", "in_store_top_n"), where_in(col("decision_list", "in_store_top_n"), [True]))]

pages = [
    Page("Review list", [
        heading("This week's review list", "Top 10 series per store by expected lost demand next week: a review "
                "list, not an order. Cards show (Blank)? Press Refresh to load outputs/powerbi.", 960),
        store_slicer(),
        *cards(92, [("decision_list", "Listed series", "Series to review"),
                    ("decision_list", "Expected lost demand, listed (7 days)", "Expected lost demand (7 days)"),
                    ("decision_list", "Low-confidence listed", "Low confidence: verify first"),
                    ("decision_list", "Realised vs expected", "Holdout: realised vs expected")]),
        V("table", 20, 198, 1240, 482, "Review first",
          {"Values": cols("decision_list", {"store_rank": "#", "product_id": "Product",
                                            "third_category_id": "Category", "expected_lost_7d": "Expected lost (7d)",
                                            "confidence": "Confidence", "reasons": "Why it is here",
                                            "checks_before_acting": "Check before acting"})},
          sort=[(col("decision_list", "store_rank"), "Ascending")], filters=TOP10),
        footer(NOTE),
    ]),
    Page("Why this series", [
        heading("Why this series", "Recorded sales vs recovered demand for one listed series, by day. "
                "The gap is demand hidden by stockouts (estimated).", 700),
        V("slicer", 740, 2, 240, 84, "Rank on the store's list", {"Values": [col("fact_series_day", "store_rank")]},
          single=True, default=[1]),
        store_slicer(),
        V("line", 20, 92, 820, 400, "Recorded vs recovered (history and outcome week)",
          {"Category": [(col("fact_series_day", "dt"), "Date")],
           "Y": [mea("fact_series_day", "Recorded sales"), mea("fact_series_day", "Recovered demand")]}),
        V("column", 860, 92, 400, 400, "Stockout hours in the selling window",
          {"Category": [(col("fact_series_day", "dt"), "Date")], "Y": [mea("fact_series_day", "Stockout hours")]}),
        V("table", 20, 502, 1240, 178, "Evidence behind the rank",
          {"Values": cols("decision_list", {"store_rank": "#", "oos_days_28": "Stockout days (28)",
                                            "typical_sellout_hour": "Typical sell-out hour",
                                            "hidden_vs_recorded": "Hidden vs recorded", "instock_days": "In-stock days",
                                            "imputed_share": "Imputed share", "confidence": "Confidence",
                                            "outcome_lost": "Holdout: realised lost",
                                            "outcome_oos_hours": "Holdout: stockout hours"})},
          sort=[(col("decision_list", "store_rank"), "Ascending")], filters=TOP10),
        footer(NOTE),
    ]),
    Page("Store availability", [
        heading("Store availability", "Selling-window availability for the selected store (all series).", 960),
        store_slicer(),
        *cards(92, [("fact_store_day", "Store recorded sales", "Recorded sales"),
                    ("fact_store_day", "Store recovered demand", "Recovered demand"),
                    ("fact_store_day", "Hidden demand vs recorded", "Hidden demand vs recorded"),
                    ("fact_store_day", "Series-days with a stockout", "Series-days with a stockout"),
                    ("fact_store_day", "Unavailable series-days", "Unavailable all day")]),
        V("line", 20, 198, 1240, 482, "Recorded vs recovered by day",
          {"Category": [(col("fact_store_day", "dt"), "Date")],
           "Y": [mea("fact_store_day", "Store recorded sales"), mea("fact_store_day", "Store recovered demand")]}),
        footer(NOTE),
    ]),
    Page("Method evidence", [
        heading("Does the ranking beat simpler rules?", "8 decision days: 7 development, 1 holdout (eval split, "
                "scored once). Store-level bootstrap intervals for the holdout differences.", 1240),
        V("line", 20, 92, 620, 300, "Lost demand captured by each store's top 10",
          {"Category": [(col("an_eval_fold", "decision_day"), "Decision day")], "Series": [col("an_eval_fold", "method")],
           "Y": [mea("an_eval_fold", "Lost demand captured (store top 10)")]},
          filters=[(col("an_eval_fold", "method"), where_in(col("an_eval_fold", "method"),
                    ["A1 Volume", "A2 Frequency", "B Lost demand", "P hl=14 m=7", "Oracle (hindsight)"]))]),
        V("table", 660, 92, 600, 300, "Holdout: proposed minus baseline",
          {"Values": cols("an_bootstrap", {"metric": "Metric", "baseline": "Baseline", "difference": "Difference",
                                           "ci_low": "95% low", "ci_high": "95% high", "verdict": "Verdict"})}),
        V("table", 20, 402, 1240, 278, "Methods by role",
          {"Values": cols("an_method_summary", {"role": "Role", "method": "Method", "folds": "Folds",
                                                "store_top10_lost_capture": "Lost demand in top 10",
                                                "worst_fold_lost_capture": "Worst fold",
                                                "store_top10_hours_capture": "Stockout hours in top 10",
                                                "global_top10pct_lost_capture": "Lost demand, global top 10%"})},
          sort=[(col("an_method_summary", "role"), "Ascending")]),
        footer("Lost demand is estimated with the recovery model; stockout hours are model-free. "
               "B and P are close: the simple rule is nearly as good."),
    ]),
    Page("Recovery check", [
        heading("Can we trust the demand estimate?", "Masking test: hide the hours after a sell-out on real "
                "in-stock days, rebuild the day, compare with the truth.", 1240),
        V("bar", 20, 92, 620, 300, "Masking error by estimator (holdout history, all sell-out times)",
          {"Category": [col("an_masking_summary", "estimator")], "Y": [mea("an_masking_summary", "Masking error (WAPE)")]},
          filters=[(col("an_masking_summary", "sellout_window"), where_in(col("an_masking_summary", "sellout_window"),
                                                                          ["1. all"])),
                   (col("an_masking_summary", "role"), where_in(col("an_masking_summary", "role"),
                                                                ["holdout (confirmation)"]))]),
        V("table", 660, 92, 600, 300, "Confidence tiers: realised vs expected (listed series)",
          {"Values": cols("an_confidence_check", {"confidence": "Tier", "series": "Series", "listed": "Listed",
                                                  "realised_over_expected_listed": "Realised / expected",
                                                  "listed_with_stockout_next_week": "Stocked out next week"})}),
        V("table", 20, 402, 1240, 278, "Does the adjustment improve a simple forecast? (truth: in-stock days)",
          {"Values": cols("an_forecast_check", {"role": "Role", "forecast": "Forecast", "days": "Days",
                                                "wape": "WAPE", "bias": "Bias"})}),
        footer("Full-day stockouts cannot be masked, so they are reported as unavailable days and kept out of the "
               "lost-demand target."),
    ]),
    Page("Data quality & lineage", [
        heading("Data quality & lineage", "Every rule reports rows flagged, what it does to a decision, and the "
                "treatment.", 1240),
        *cards(92, [("qa_dq_results", "DQ checks passed", "PASS"), ("qa_dq_results", "DQ checks warned", "WARN"),
                    ("qa_dq_results", "DQ checks failed", "FAIL")]),
        V("table", 20, 198, 820, 300, "Data quality rules",
          {"Values": cols("qa_dq_results", {"rule_id": "Rule", "check": "Check", "status": "Status",
                                            "rows_flagged": "Rows flagged", "rate": "Rate",
                                            "decision_impact": "Decision impact", "treatment": "Treatment"})},
          sort=[(col("qa_dq_results", "rule_id"), "Ascending")]),
        V("line", 860, 198, 400, 300, "Stock flags vs sales by hour (DQ07)",
          {"Category": [(col("an_hour_profile", "hour"), "Hour")],
           "Y": [mea("an_hour_profile", "Flagged out of stock"), mea("an_hour_profile", "Share of daily sales")]}),
        V("table", 20, 508, 500, 172, "Row counts through the pipeline",
          {"Values": cols("qa_row_lineage", {"step_no": "#", "step": "Step", "rows": "Rows"})},
          sort=[(col("qa_row_lineage", "step_no"), "Ascending")]),
        V("table", 540, 508, 720, 172, "Source files",
          {"Values": cols("gov_source_manifest", {"split": "Split", "rows": "Rows", "first_date": "From",
                                                  "last_date": "To", "sha256": "SHA-256"})}),
        footer(NOTE),
    ]),
]

if __name__ == "__main__":
    print(build(ROOT / "powerbi", "Availability_to_Action", DATA, tables, relationships, pages))
