"""Censored demand recovery, run once per decision date (fold).

For a decision made at day T only days T-28..T-1 are visible. From those days we estimate, per
series, (a) the hourly sales profile on fully in-stock days, shrunk toward the third-category profile,
and (b) the typical in-stock day (mean selling-window sales on fully in-stock days).

A day with stockout hours is recovered as

    demand = in-stock sales + (1 - coverage) * typical in-stock day,   floored at recorded sales

where coverage is the share of the hourly profile that falls in the hours the item was in stock. This
is the coverage-weighted blend of the profile estimate (in-stock sales / coverage) and the typical day,
chosen on the development fold masking test (masking_test) over the pure profile and the pure mean.
Full-day stockouts have coverage 0, so they take the typical day; that case cannot be validated by
masking, so it is reported separately ('unavailable') and kept out of the primary lost-demand target.

The same history estimates recover the outcome week T..T+6, so the evaluation target never uses
information from after T, except the outcome week's own sales and stock flags.
"""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
P = json.loads((ROOT / "config" / "params.json").read_text(encoding="utf-8"))
H = 16                                                     # hours in the selling window
HIST, HORIZON = P["history_days"], P["horizon_days"]
K = P["recovery"]["profile_shrinkage_days"]
MIN_COV = P["recovery"]["min_coverage"]
DEV_MASK_FOLD = P["recovery"]["method_selection_fold"]
HOLDOUT = P["holdout_decision_day"]
FOLDS = P["dev_decision_days"] + [HOLDOUT]


def _sums():
    return ", ".join(f"sum(h[{i}]) AS p{i}" for i in range(1, H + 1))


def _profile_list():
    """Shrunk profile as a 16-element list: w * series share + (1 - w) * category share, w = n / (n + K)."""
    n, tot = "coalesce(ps.n, 0)", "coalesce(ps.tot, 0)"
    parts = [f"CASE WHEN {tot} > 0 THEN {n} / ({n} + {K}) * ps.p{i} / ps.tot ELSE 0 END "
             f"+ CASE WHEN {tot} > 0 THEN {K} / ({n} + {K}) ELSE 1 END * coalesce(pc.p{i} / nullif(pc.tot, 0), 1.0 / {H})"
             for i in range(1, H + 1)]
    return "list_value(" + ", ".join(parts) + ")"


def build_profiles(con, T):
    lo, hi = T - HIST, T - 1
    con.execute(f"""
        CREATE OR REPLACE TEMP TABLE _ps AS
        SELECT series_id, count(*) FILTER (WHERE window_sales > 0)::DOUBLE AS n, count(*) AS instock_days,
               sum(window_sales) AS tot, {_sums()}
        FROM stg_daily WHERE day_idx BETWEEN {lo} AND {hi} AND oos_hours = 0 GROUP BY series_id;
        CREATE OR REPLACE TEMP TABLE _pc AS
        SELECT third_category_id, sum(window_sales) AS tot, {_sums()}
        FROM stg_daily WHERE day_idx BETWEEN {lo} AND {hi} AND oos_hours = 0 GROUP BY third_category_id;
        CREATE OR REPLACE TEMP TABLE _profile AS
        SELECT d.series_id, coalesce(ps.instock_days, 0) AS instock_days,
               ps.tot / ps.instock_days AS typical_day, {_profile_list()} AS p
        FROM dim_series d
        LEFT JOIN _ps ps USING (series_id)
        LEFT JOIN _pc pc USING (third_category_id);   -- a category with no in-stock day: flat profile
    """)


def recover_fold(con, T):
    """Recover demand for history and outcome days of decision day T; appends to rec_daily."""
    build_profiles(con, T)
    lo, hi = T - HIST, T + HORIZON - 1
    con.execute(f"""
        INSERT INTO rec_daily
        WITH r AS (
            SELECT d.series_id, d.day_idx, d.window_sales, d.instock_sales, d.oos_hours, d.first_oos_hour,
                   d.dq_sales_after_sellout, pr.instock_days, pr.typical_day,
                   list_sum(list_transform(d.s, lambda x, i: CASE WHEN x = 0 THEN pr.p[i] ELSE 0 END)) AS coverage
            FROM stg_daily d JOIN _profile pr USING (series_id)
            WHERE d.day_idx BETWEEN {lo} AND {hi}),
        e AS (
            SELECT *,
                CASE WHEN oos_hours = 0 THEN 'observed' WHEN oos_hours = {H} THEN 'unavailable' ELSE 'partial' END
                    AS method,
                CASE WHEN oos_hours = 0 THEN window_sales
                     WHEN typical_day IS NOT NULL THEN greatest(window_sales, instock_sales + (1 - coverage) * typical_day)
                     -- no in-stock day in history: profile scaling if the in-stock hours say enough, else as recorded
                     WHEN coverage >= {MIN_COV} THEN greatest(window_sales, instock_sales / coverage)
                     ELSE window_sales END AS rec
            FROM r)
        SELECT {T}, series_id, day_idx, day_idx >= {T}, method, oos_hours, first_oos_hour, dq_sales_after_sellout,
               instock_days, typical_day IS NULL AS no_typical_day, coverage, window_sales, rec, rec - window_sales
        FROM e
    """)


def masking_test(con, T, role):
    """Hide the tail of real in-stock days of fold T's history; compare estimators with the truth."""
    build_profiles(con, T)
    lo, hi = T - HIST, T - 1
    seed = P["masking_test"]["seed"]
    hidden = ", ".join(f"ps.p{i} - m.h[{i}]" for i in range(1, H + 1))
    cat = ", ".join(f"pc.p{i} / pc.tot" for i in range(1, H + 1))
    con.execute(f"""
        -- observed sell-out start hours: partial-day stockouts that run unbroken to the end of the window
        CREATE OR REPLACE TEMP TABLE _start_dist AS
        WITH x AS (
            SELECT first_oos_hour AS start_hour, count(*) AS n FROM stg_daily
            WHERE day_idx BETWEEN {lo} AND {hi} AND first_oos_hour >= 7 AND oos_hours = 22 - first_oos_hour
            GROUP BY 1)
        SELECT start_hour, n, sum(n) OVER (ORDER BY start_hour) / sum(n) OVER () AS cum_hi,
               (sum(n) OVER (ORDER BY start_hour) - n) / sum(n) OVER () AS cum_lo FROM x;
        INSERT INTO an_masking_days
        WITH days AS (
            SELECT d.series_id, d.third_category_id, d.day_idx, d.h, d.window_sales AS truth,
                   (hash(d.series_id, d.day_idx, {seed}) % 1000000) / 1000000.0 AS u
            FROM stg_daily d WHERE d.day_idx BETWEEN {lo} AND {hi} AND d.oos_hours = 0 AND d.window_sales > 0),
        m AS (
            SELECT days.*, sd.start_hour, sd.start_hour - 5 AS cut       -- first hidden list position
            FROM days JOIN _start_dist sd ON days.u >= sd.cum_lo AND days.u < sd.cum_hi),
        loo AS (   -- leave-one-out: the masked day never informs its own profile or typical day
            SELECT m.*, ps.n - 1 AS n_loo, ps.instock_days - 1 AS days_loo, ps.tot - m.truth AS tot_loo,
                   list_value({hidden}) AS ps_loo, list_value({cat}) AS pc_share
            FROM m JOIN _ps ps USING (series_id) JOIN _pc pc USING (third_category_id)),
        c AS (
            SELECT series_id, day_idx, start_hour, truth, days_loo,
                   list_sum(list_transform(h, lambda x, i: CASE WHEN i < cut THEN x ELSE 0 END)) AS observed,
                   CASE WHEN days_loo > 0 THEN tot_loo / days_loo END AS typical_day,
                   list_sum(list_transform(ps_loo, lambda x, i: CASE WHEN i < cut THEN
                       CASE WHEN tot_loo > 0 THEN n_loo / (n_loo + {K}) * x / tot_loo ELSE 0 END
                       + CASE WHEN tot_loo > 0 THEN {K} / (n_loo + {K}) ELSE 1 END * pc_share[i]
                       ELSE 0 END)) AS coverage
            FROM loo)
        SELECT '{role}', {T}, *,
               greatest(observed, coalesce(typical_day, observed)) AS est_typical_day,
               CASE WHEN coverage >= {MIN_COV} THEN observed / coverage
                    ELSE greatest(observed, coalesce(typical_day, observed)) END AS est_profile,
               greatest(observed, observed + (1 - coverage) * coalesce(typical_day, observed / nullif(coverage, 0), 0))
                   AS est_blend
        FROM c
    """)


ESTIMATORS = {"observed": "A. Recorded sales (no adjustment)", "est_typical_day": "B. Typical in-stock day",
              "est_profile": "C. Hourly profile scaling", "est_blend": "D. Coverage-weighted blend (used)"}


def run(con):
    con.execute("""CREATE OR REPLACE TABLE rec_daily (fold INT, series_id BIGINT, day_idx INT, is_outcome BOOLEAN,
        method VARCHAR, oos_hours INT, first_oos_hour INT, dq_sales_after_sellout BOOLEAN, instock_days INT,
        no_typical_day BOOLEAN, coverage DOUBLE, window_sales DOUBLE, rec DOUBLE, lost DOUBLE)""")
    for T in FOLDS:
        recover_fold(con, T)
    print(f"recovered {len(FOLDS)} folds: {con.execute('SELECT count(*) FROM rec_daily').fetchall()[0][0]:,d} rows")
    con.execute("""CREATE OR REPLACE TABLE an_masking_days (role VARCHAR, fold INT, series_id BIGINT, day_idx INT,
        start_hour INT, truth DOUBLE, days_loo INT, observed DOUBLE, typical_day DOUBLE, coverage DOUBLE,
        est_typical_day DOUBLE, est_profile DOUBLE, est_blend DOUBLE)""")
    masking_test(con, DEV_MASK_FOLD, "development (method chosen here)")
    masking_test(con, HOLDOUT, "holdout (confirmation)")
    long = " UNION ALL ".join(f"SELECT role, start_hour, truth, '{label}' AS estimator, {c} AS est FROM an_masking_days"
                              for c, label in ESTIMATORS.items())
    con.execute(f"""
        CREATE OR REPLACE TABLE an_masking_summary AS
        WITH long AS ({long}),
        b AS (SELECT *, CASE WHEN start_hour <= 13 THEN '2. sell-out by 13:00'
                             WHEN start_hour <= 17 THEN '3. sell-out 14:00 to 17:00'
                             ELSE '4. sell-out 18:00 or later' END AS sellout_window FROM long)
        SELECT role, '1. all' AS sellout_window, estimator, count(*) AS days,
               sum(abs(est - truth)) / sum(truth) AS wape, sum(est - truth) / sum(truth) AS bias
        FROM b GROUP BY ALL
        UNION ALL
        SELECT role, sellout_window, estimator, count(*), sum(abs(est - truth)) / sum(truth),
               sum(est - truth) / sum(truth) FROM b GROUP BY ALL
        ORDER BY role, sellout_window, estimator
    """)
