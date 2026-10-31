"""Backtest: does a method put the right series at the top of the review list?

Primary metric (chosen before any holdout result was seen): share of next-week lost demand that falls in
each store's top-10 list, summed over stores. Secondary: the same for stockout hours (model-free, so it
cannot favour methods that share the recovery model with the target) and for a global top-10% list.

P's half-life and shrinkage are picked on the 7 development folds; the holdout week (eval split,
26-Jun to 02-Jul-2024) is scored once, with store-level bootstrap intervals for each difference.
"""
import json
from pathlib import Path

import numpy as np
import pandas as pd

import prioritise

ROOT = Path(__file__).resolve().parents[1]
P = json.loads((ROOT / "config" / "params.json").read_text(encoding="utf-8"))
PR = P["prioritisation"]
HOLDOUT = P["holdout_decision_day"]
N, TOP = PR["store_top_n"], PR["top_share"]
BASELINES = ["A1 Volume", "A2 Frequency", "B Lost demand"]


def metrics(con):
    con.execute(f"""
        CREATE OR REPLACE TABLE eval_store AS     -- one row per fold x method x store: captured and total
        SELECT s.fold, s.method, s.store_id,
               sum(o.y_lost) FILTER (WHERE s.store_rank <= {N}) AS lost_captured, sum(o.y_lost) AS lost_total,
               sum(o.y_oos_hours) FILTER (WHERE s.store_rank <= {N}) AS hours_captured, sum(o.y_oos_hours) AS hours_total,
               sum(o.y_lost) FILTER (WHERE s.global_rank <= {TOP} * 50000) AS lost_captured_global,
               sum(o.y_unavailable_days) FILTER (WHERE s.store_rank <= {N}) AS unavail_captured,
               sum(o.y_unavailable_days) AS unavail_total
        FROM scores s JOIN outcome o USING (fold, series_id) GROUP BY ALL;
        CREATE OR REPLACE TABLE eval_fold AS
        SELECT fold, CASE WHEN fold = {HOLDOUT} THEN 'holdout' ELSE 'development' END AS role, method,
               sum(lost_captured) / sum(lost_total) AS store_top10_lost_capture,
               sum(hours_captured) / sum(hours_total) AS store_top10_hours_capture,
               sum(lost_captured_global) / sum(lost_total) AS global_top10pct_lost_capture,
               sum(unavail_captured) / sum(unavail_total) AS store_top10_unavailable_capture
        FROM eval_store GROUP BY ALL ORDER BY fold, method;
    """)


def choose_variant(con):
    dev = con.execute("""SELECT method, avg(store_top10_lost_capture) AS dev_mean FROM eval_fold
                         WHERE role = 'development' AND method LIKE 'P %' GROUP BY method
                         ORDER BY dev_mean DESC, method""").df()
    chosen = dev.iloc[0]["method"]
    con.register("dev", dev.assign(chosen=dev["method"] == chosen))
    con.execute("CREATE OR REPLACE TABLE an_variant_selection AS SELECT * FROM dev")
    con.unregister("dev")
    return chosen


def summary(con, chosen):
    con.execute(f"""
        CREATE OR REPLACE TABLE an_method_summary AS
        SELECT role, CASE WHEN method = '{chosen}' THEN 'P Expected loss (' || substr(method, 3) || ')' ELSE method END
                   AS method,
               method = '{chosen}' AS is_proposed, count(*) AS folds,
               avg(store_top10_lost_capture) AS store_top10_lost_capture,
               min(store_top10_lost_capture) AS worst_fold_lost_capture,
               avg(store_top10_hours_capture) AS store_top10_hours_capture,
               avg(global_top10pct_lost_capture) AS global_top10pct_lost_capture,
               avg(store_top10_unavailable_capture) AS store_top10_unavailable_capture
        FROM eval_fold WHERE method IN ({", ".join(f"'{m}'" for m in BASELINES)}, '{chosen}', 'Oracle (hindsight)')
        GROUP BY ALL ORDER BY role, method
    """)


def bootstrap(con, chosen):
    """Holdout difference chosen - baseline, resampling stores with replacement."""
    df = con.execute(f"SELECT * FROM eval_store WHERE fold = {HOLDOUT}").df()
    rng = np.random.default_rng(PR["seed"])
    stores = np.sort(df["store_id"].unique())
    idx = rng.integers(0, len(stores), size=(PR["bootstrap_reps"], len(stores)))
    rows = []
    for metric, cap, tot in [("store_top10_lost_capture", "lost_captured", "lost_total"),
                             ("store_top10_hours_capture", "hours_captured", "hours_total")]:
        def arrays(m):
            g = df[df["method"] == m].set_index("store_id").reindex(stores).fillna(0)
            return g[cap].to_numpy(), g[tot].to_numpy()
        pc, pt = arrays(chosen)
        for base in BASELINES:
            bc, bt = arrays(base)
            point = pc.sum() / pt.sum() - bc.sum() / bt.sum()
            boot = pc[idx].sum(1) / pt[idx].sum(1) - bc[idx].sum(1) / bt[idx].sum(1)
            lo, hi = np.percentile(boot, [2.5, 97.5])
            rows.append(dict(metric=metric, proposed=chosen, baseline=base, difference=point, ci_low=lo, ci_high=hi,
                             verdict="better" if lo > 0 else "worse" if hi < 0 else "no clear difference"))
    out = pd.DataFrame(rows)
    con.register("b", out)
    con.execute("CREATE OR REPLACE TABLE an_bootstrap AS SELECT * FROM b")
    con.unregister("b")


def forecast_check(con):
    """Does the stockout adjustment reduce forecast bias? Truth = next-week days with no stockout hours."""
    con.execute("""
        CREATE OR REPLACE TABLE an_forecast_check AS
        WITH f AS (SELECT fold, series_id, obs_28 / 28.0 AS f_recorded, rec_28 / 28.0 AS f_recovered FROM feat),
        t AS (SELECT r.fold, r.series_id, r.window_sales AS actual FROM rec_daily r
              WHERE r.is_outcome AND r.oos_hours = 0)
        SELECT CASE WHEN fold = 91 THEN 'holdout' ELSE 'development' END AS role, forecast, count(*) AS days,
               sum(abs(fc - actual)) / sum(actual) AS wape, sum(fc - actual) / sum(actual) AS bias
        FROM (SELECT t.fold, t.actual, 'Mean of recorded sales' AS forecast, f.f_recorded AS fc FROM t JOIN f USING (fold, series_id)
              UNION ALL
              SELECT t.fold, t.actual, 'Mean of recovered demand', f.f_recovered FROM t JOIN f USING (fold, series_id))
        GROUP BY ALL ORDER BY role, forecast
    """.replace("fold = 91", f"fold = {HOLDOUT}"))


def confidence_check(con):
    """Is the confidence tier informative? (a) masking error on holdout-history days by the series' tier;
    (b) for listed series, realised next-week lost demand / expected (calibration)."""
    con.execute(f"""
        CREATE OR REPLACE TABLE an_confidence_check AS
        WITH m AS (
            SELECT d.confidence, count(*) AS masked_days,
                   sum(abs(m.est_blend - m.truth)) / sum(m.truth) AS masking_wape
            FROM an_masking_days m JOIN decision_list d USING (series_id)
            WHERE m.role LIKE 'holdout%' GROUP BY 1),
        c AS (
            SELECT confidence, count(*) AS series, count(*) FILTER (WHERE in_store_top_n) AS listed,
                   sum(outcome_lost) FILTER (WHERE in_store_top_n) / sum(expected_lost_7d) FILTER (WHERE in_store_top_n)
                       AS realised_over_expected_listed,
                   avg((outcome_oos_hours > 0)::INT) FILTER (WHERE in_store_top_n) AS listed_with_stockout_next_week
            FROM decision_list GROUP BY 1)
        SELECT c.*, m.masked_days, m.masking_wape FROM c LEFT JOIN m USING (confidence)
        ORDER BY CASE c.confidence WHEN 'High' THEN 1 WHEN 'Medium' THEN 2 ELSE 3 END
    """)


def run(con):
    prioritise.features(con)
    prioritise.scores(con)
    metrics(con)
    chosen = choose_variant(con)
    summary(con, chosen)
    bootstrap(con, chosen)
    prioritise.decision_list(con, chosen)
    forecast_check(con)
    confidence_check(con)
    print(f"proposed variant chosen on development folds: {chosen}")
    return chosen
