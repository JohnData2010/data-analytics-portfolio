"""Data quality rules: what was checked, what was found, and what each finding does to a decision.

Status: PASS = rule holds; WARN = finding that changes a definition or a treatment (documented below);
FAIL = rule broken in a way the pipeline cannot absorb (the run stops).
"""
import pandas as pd


def _one(con, sql):
    return con.execute(sql).fetchall()[0]


def run(con):
    rows = []

    def add(rule_id, check, severity, checked, flagged, impact, treatment, fail_if_flagged=False):
        status = "PASS" if flagged == 0 else ("FAIL" if fail_if_flagged else "WARN")
        rows.append(dict(rule_id=rule_id, check=check, severity=severity, rows_checked=int(checked),
                         rows_flagged=int(flagged), rate=flagged / checked if checked else None,
                         decision_impact=impact, treatment=treatment, status=status))

    n = _one(con, "SELECT count(*) FROM stg_daily")[0]
    raw = _one(con, "SELECT (SELECT count(*) FROM raw_train) + (SELECT count(*) FROM raw_eval)")[0]

    dup = _one(con, "SELECT count(*) FROM (SELECT series_id, dt FROM stg_daily GROUP BY ALL HAVING count(*) > 1)")[0]
    add("DQ01", "Grain: one row per store, product and date", "critical", n, dup,
        "Duplicate days would double-count demand and stockout hours", "None needed", True)

    gaps = _one(con, """SELECT count(*) FROM (SELECT series_id, count(*) AS d, min(day_idx) AS a, max(day_idx) AS b
                        FROM stg_daily GROUP BY 1) WHERE d <> 97 OR a <> 1 OR b <> 97""")[0]
    add("DQ02", "Complete panel: every series has 90 train + 7 eval consecutive days", "critical",
        _one(con, "SELECT count(DISTINCT series_id) FROM stg_daily")[0], gaps,
        "Missing days would read as zero demand in the 28-day features", "None needed", True)

    add("DQ03", "Hourly sales sum to daily sales", "high", n,
        _one(con, "SELECT count(*) FROM stg_daily WHERE dq_hourly_sum_mismatch")[0],
        "Hourly recovery would not reconcile with reported daily sales", "None needed", True)

    add("DQ04", "stock_hour6_22_cnt equals the hourly flags for hours 6 to 21", "high", n,
        _one(con, "SELECT count(*) FROM stg_daily WHERE dq_oos_count_mismatch")[0],
        "Confirms the publisher's selling window is 06:00 to 21:59 (16 hours), not 06:00 to 22:59",
        "Selling window fixed at hours 6 to 21 in config/params.json", True)

    bad_h = _one(con, """SELECT (SELECT count(*) FROM (SELECT product_id FROM dim_series GROUP BY 1
                                 HAVING count(DISTINCT third_category_id) > 1))
                              + (SELECT count(*) FROM (SELECT store_id FROM dim_series GROUP BY 1
                                 HAVING count(DISTINCT city_id) > 1))""")[0]
    add("DQ05", "Hierarchy: each product in one category path, each store in one city", "medium",
        _one(con, "SELECT count(DISTINCT product_id) + count(DISTINCT store_id) FROM dim_series")[0], bad_h,
        "Category and city roll-ups would split or double-count", "None needed", True)

    conflict, conflict_share = _one(con, """
        SELECT count(*) FILTER (WHERE dq_sales_after_sellout),
               sum(list_sum(list_transform(h, lambda x, i: CASE WHEN i > 1 AND s[i] = 1 AND s[i - 1] = 1 THEN x ELSE 0 END)))
                   / sum(window_sales)
        FROM stg_daily""")
    any_oos_sales = _one(con, "SELECT count(*) FROM stg_daily WHERE dq_sales_in_oos_hour")[0]
    add("DQ06", "Sales recorded in an out-of-stock hour that follows another out-of-stock hour", "high", n, conflict,
        f"{any_oos_sales:,d} days have sales in some flagged hour; most are the sell-out hour itself (sold out "
        f"mid-hour). The stricter rule leaves {conflict:,d} days ({conflict_share:.1%} of window sales) where the "
        "flag and the till disagree, so a flag is evidence of a stockout, not proof",
        "Recorded sales are kept and recovered demand is floored at them; series with 3+ such days get a "
        "'check the stock-status feed' item on the review list")

    r24, rwin, any24, anywin = _one(con, """
        SELECT avg(oos_hours_24h / 24.0), avg(oos_hours / 16.0), avg((oos_hours_24h > 0)::INT), avg((oos_hours > 0)::INT)
        FROM stg_daily""")
    night = _one(con, "SELECT count(*) FROM stg_daily WHERE oos_hours_24h > oos_hours")[0]
    carry = _one(con, """SELECT avg(prev_21h::INT) FROM (
                            SELECT oos_at_midnight, lag(s[16]) OVER (PARTITION BY series_id ORDER BY day_idx) = 1 AS prev_21h
                            FROM stg_daily) WHERE oos_at_midnight AND prev_21h IS NOT NULL""")[0]
    add("DQ07", "Out-of-stock flags outside the selling window (22:00 to 05:59)", "high", n, night,
        f"Counting all 24 hours puts {r24:.1%} of hours and {any24:.1%} of days out of stock; the selling window "
        f"gives {rwin:.1%} of hours and {anywin:.1%} of days. {carry:.0%} of 00:00 flags follow a stockout at 21:00 "
        "the evening before, so a 24-hour KPI counts one evening sell-out on two days",
        "Every availability KPI uses the selling window only")

    full, full_sales = _one(con, "SELECT count(*) FILTER (WHERE dq_full_day_oos), count(*) FILTER (WHERE dq_full_day_oos AND sale_amount > 0) FROM stg_daily")
    lost_share = _one(con, f"""SELECT sum(lost) FILTER (WHERE method = 'unavailable') / sum(lost)
                               FROM rec_daily WHERE fold = (SELECT max(fold) FROM rec_daily)""")[0]
    add("DQ08", "Out of stock for the whole selling window", "medium", n, full,
        f"No in-stock hour to recover from ({full_sales:,d} of these days still record sales). Imputing a typical "
        f"day would add {lost_share:.0%} to estimated lost demand, an assumption the masking test cannot check "
        "(a whole-day stockout may also be a ranging decision)",
        "Reported as 'unavailable days', kept out of the lost-demand target and the scores")

    disc = _one(con, "SELECT count(*) FROM stg_daily WHERE dq_discount_out_of_range")[0]
    zero_disc_sales = _one(con, """SELECT avg(sale_amount) FILTER (WHERE discount <= 0) / avg(sale_amount) FILTER (WHERE discount > 0 AND discount <= 1)
                                   FROM stg_daily""")[0]
    add("DQ09", "Discount within (0, 1]", "low", n, disc,
        f"discount = 0 (published meaning: free) on these rows comes with sales {zero_disc_sales:.0f}x the "
        "average: more likely a missing-value code than a giveaway. Discount is not used by any score",
        "Flagged only; would need publisher confirmation before any price-effect analysis")

    add("DQ10", "No negative sales", "critical", n,
        _one(con, "SELECT count(*) FROM stg_daily WHERE dq_negative_sales")[0], "Negative demand", "None needed", True)

    df = pd.DataFrame(rows)
    con.register("dq", df)
    con.execute("CREATE OR REPLACE TABLE qa_dq_results AS SELECT * FROM dq")
    con.unregister("dq")

    lineage = [("raw parquet rows (train + eval)", raw), ("stg_daily rows", n)]
    for fold, cnt in con.execute("SELECT fold, count(*) FROM rec_daily GROUP BY 1 ORDER BY 1").fetchall():
        lineage.append((f"rec_daily rows, decision day {fold} (35 days x 50,000 series)", cnt))
    for fold, cnt in con.execute("SELECT fold, count(*) FROM feat GROUP BY 1 ORDER BY 1").fetchall()[-1:]:
        lineage.append((f"series scored, decision day {fold}", cnt))
    lineage.append(("decision_list rows", _one(con, "SELECT count(*) FROM decision_list")[0]))
    con.register("lin", pd.DataFrame(lineage, columns=["step", "rows"]))
    con.execute("CREATE OR REPLACE TABLE qa_row_lineage AS SELECT row_number() OVER () AS step_no, * FROM lin")
    con.unregister("lin")
    fails = df[df["status"] == "FAIL"]
    print(f"dq: {len(df)} rules, {(df.status == 'PASS').sum()} pass, {(df.status == 'WARN').sum()} warn, {len(fails)} fail")
    return len(fails) == 0
