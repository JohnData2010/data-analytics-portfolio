"""Decision prioritisation: which store-product series should a planner review first, and why.

Every method scores each series at decision day T from days T-28..T-1 only:

  A1  Volume           recorded sales (the 'protect the top sellers' rule)
  A2  Frequency        days with a stockout, ties broken by recorded sales (the 'repeat offender' rule)
  B   Lost demand      28-day recovered minus recorded demand on partial stockout days
  P   Expected loss    7 x recency-weighted recovered demand x unserved share, the share shrunk toward the
                       store x third-category share; half-life and shrinkage are chosen on development folds.
                       With no recency weighting and no shrinkage P equals B, so B is nested in P's grid.

Outcomes (T..T+6) are measured, never scored: lost demand on partial stockout days (y_lost), stockout
hours in the selling window (y_oos_hours, model-free) and full-day stockouts (y_unavailable_days).
"""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
P = json.loads((ROOT / "config" / "params.json").read_text(encoding="utf-8"))
PR, CONF = P["prioritisation"], P["confidence"]
HIST, HORIZON = P["history_days"], P["horizon_days"]
SHRINK_GRID = [0, 7]


def variant(hl, m):
    return f"P hl={hl:g} m={m}"


def features(con):
    ew = []
    for hl in PR["half_life_grid"]:
        # weights normalised to average 1 over the 28 days, so sum(w) = 28 'effective days'
        w = f"(pow(0.5, (fold - 1 - day_idx) / {hl}) * {HIST} / sum(pow(0.5, (fold - 1 - day_idx) / {hl})) OVER (PARTITION BY fold, series_id))"
        ew.append(f"{w} AS w_{str(hl).replace('.', '_')}")
    con.execute(f"""
        CREATE OR REPLACE TABLE feat AS
        WITH h AS (
            SELECT *, CASE WHEN method = 'partial' THEN lost ELSE 0 END AS lost_partial, {", ".join(ew)}
            FROM rec_daily WHERE NOT is_outcome)
        SELECT fold, h.series_id, any_value(d.store_id) AS store_id, any_value(d.third_category_id) AS third_category_id,
               sum(window_sales) AS obs_28, sum(rec) AS rec_28, sum(lost_partial) AS lost_28,
               sum(lost) AS lost_incl_unavailable_28,
               count(*) FILTER (WHERE oos_hours > 0) AS oos_days_28,
               count(*) FILTER (WHERE oos_hours > 0 AND day_idx >= fold - 7) AS oos_days_7,
               sum(oos_hours) AS oos_hours_28,
               count(*) FILTER (WHERE method = 'unavailable') AS unavailable_days_28,
               count(*) FILTER (WHERE dq_sales_after_sellout) AS conflict_days_28,
               any_value(instock_days) AS instock_days,
               median(first_oos_hour) FILTER (WHERE method = 'partial') AS typical_sellout_hour,
               {", ".join(f"sum(w_{str(hl).replace('.', '_')} * rec) AS rec_w_{str(hl).replace('.', '_')}, "
                          f"sum(w_{str(hl).replace('.', '_')} * lost_partial) AS lost_w_{str(hl).replace('.', '_')}"
                          for hl in PR["half_life_grid"])}
        FROM h JOIN dim_series d USING (series_id)
        GROUP BY fold, h.series_id;
        -- pooled unserved share by store x third category (shrinkage target)
        CREATE OR REPLACE TABLE feat_pool AS
        SELECT fold, store_id, third_category_id, sum(lost_28) / nullif(sum(rec_28), 0) AS pool_share
        FROM feat GROUP BY ALL;
        CREATE OR REPLACE TABLE outcome AS
        SELECT fold, series_id,
               sum(CASE WHEN method = 'partial' THEN lost ELSE 0 END) AS y_lost,
               sum(oos_hours) AS y_oos_hours,
               count(*) FILTER (WHERE method = 'unavailable') AS y_unavailable_days,
               sum(window_sales) AS y_recorded, sum(rec) AS y_recovered
        FROM rec_daily WHERE is_outcome GROUP BY ALL;
    """)


def scores(con):
    parts = ["SELECT fold, series_id, 'A1 Volume' AS method, obs_28 AS score FROM feat",
             "SELECT fold, series_id, 'A2 Frequency', oos_days_28 + obs_28 / (1 + max(obs_28) OVER (PARTITION BY fold)) FROM feat",
             "SELECT fold, series_id, 'B Lost demand', lost_28 FROM feat"]
    for hl in PR["half_life_grid"]:
        k = str(hl).replace(".", "_")
        for m in SHRINK_GRID:
            share = (f"(f.lost_w_{k} + {m} * (f.rec_w_{k} / {HIST}) * coalesce(p.pool_share, 0)) "
                     f"/ nullif(f.rec_w_{k} + {m} * (f.rec_w_{k} / {HIST}), 0)")
            parts.append(f"SELECT f.fold, f.series_id, '{variant(hl, m)}', "
                         f"coalesce({HORIZON} * f.rec_w_{k} / {HIST} * {share}, 0) "
                         f"FROM feat f LEFT JOIN feat_pool p USING (fold, store_id, third_category_id)")
    parts.append("SELECT fold, series_id, 'Oracle (hindsight)', y_lost FROM outcome")
    con.execute(f"""
        CREATE OR REPLACE TABLE scores AS
        WITH s AS ({" UNION ALL ".join(parts)})
        SELECT s.*, f.store_id,
               row_number() OVER (PARTITION BY s.fold, s.method, f.store_id
                                  ORDER BY s.score DESC, f.obs_28 DESC, s.series_id) AS store_rank,
               row_number() OVER (PARTITION BY s.fold, s.method ORDER BY s.score DESC, f.obs_28 DESC, s.series_id)
                   AS global_rank
        FROM s JOIN feat f USING (fold, series_id)
    """)


def decision_list(con, chosen):
    """Holdout-week review list: chosen P variant, confidence tier, reasons and checks for every series."""
    T = P["holdout_decision_day"]
    n = PR["store_top_n"]
    con.execute(f"""
        CREATE OR REPLACE TABLE decision_list AS
        WITH s AS (SELECT series_id, score, store_rank, global_rank FROM scores WHERE fold = {T} AND method = '{chosen}'),
        base AS (
            SELECT f.*, s.score AS expected_lost_7d, s.store_rank, s.global_rank,
                   f.lost_incl_unavailable_28 / nullif(f.rec_28, 0) AS imputed_share,
                   f.lost_28 / nullif(f.obs_28, 0) AS hidden_vs_recorded,
                   percent_rank() OVER (PARTITION BY f.store_id ORDER BY f.rec_28) AS demand_pctile_in_store
            FROM feat f JOIN s USING (series_id) WHERE f.fold = {T}),
        t AS (
            SELECT *, CASE
                WHEN imputed_share > {CONF['low_max_imputed_share']} OR instock_days < {CONF['low_min_instock_days']} THEN 'Low'
                WHEN imputed_share <= {CONF['high_max_imputed_share']} AND instock_days >= {CONF['high_min_instock_days']} THEN 'High'
                ELSE 'Medium' END AS confidence
            FROM base)
        SELECT t.series_id, t.store_id, d.city_id, d.product_id, d.first_category_id, d.second_category_id,
               d.third_category_id, t.store_rank, t.global_rank, t.store_rank <= {n} AS in_store_top_n,
               t.expected_lost_7d, t.obs_28, t.rec_28, t.lost_28, t.imputed_share, t.hidden_vs_recorded,
               t.oos_days_28, t.oos_days_7, t.oos_hours_28, t.unavailable_days_28, t.conflict_days_28,
               t.instock_days, t.typical_sellout_hour, t.confidence,
               concat_ws('; ',
                   CASE WHEN t.oos_days_28 >= 14 THEN 'Stocks out on ' || t.oos_days_28 || ' of the last 28 days' END,
                   CASE WHEN t.typical_sellout_hour <= 17 THEN 'Typically sells out by '
                        || lpad(CAST(CAST(t.typical_sellout_hour AS INT) AS VARCHAR), 2, '0') || ':00' END,
                   CASE WHEN t.oos_days_7 >= 5 AND t.oos_days_7 / 7.0 > 1.25 * t.oos_days_28 / 28.0
                        THEN 'Stockouts rising: ' || t.oos_days_7 || ' of the last 7 days' END,
                   CASE WHEN t.hidden_vs_recorded >= 0.25 THEN 'Estimated demand '
                        || CAST(round(100 * t.hidden_vs_recorded) AS INT) || '% above recorded sales' END,
                   CASE WHEN t.demand_pctile_in_store >= 0.8 THEN 'Top 20% of store demand' END) AS reasons,
               concat_ws('; ',
                   CASE WHEN t.confidence = 'Low' THEN 'Demand estimate mostly imputed: confirm with on-hand stock before ordering more' END,
                   CASE WHEN t.unavailable_days_28 >= 3 THEN 'Unavailable all day on ' || t.unavailable_days_28
                        || ' days: check ranging, delisting or supply' END,
                   CASE WHEN t.conflict_days_28 >= 3 THEN 'Sales recorded after the sell-out hour on ' || t.conflict_days_28
                        || ' days: check the stock-status feed' END,
                   'Check on-hand stock, open orders and lead time (not in this dataset)') AS checks_before_acting,
               o.y_lost AS outcome_lost, o.y_oos_hours AS outcome_oos_hours,
               o.y_unavailable_days AS outcome_unavailable_days, o.y_recorded AS outcome_recorded
        FROM t JOIN dim_series d USING (series_id)
        JOIN outcome o ON o.fold = {T} AND o.series_id = t.series_id
        ORDER BY t.store_id, t.store_rank
    """)
