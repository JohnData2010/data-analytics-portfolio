"""Reconciliation against NDIA sources.

Three kinds of check:
  * load    - every raw row reached DuckDB
  * internal - published hierarchies add up (states -> national, districts -> state)
  * cross-source - CSV extracts agree with the NDIA SDA supplement (XLSX) for 30-Jun-2026
Results are written to outputs/reconciliation.csv and table qa_reconciliation.
"""
from pathlib import Path

import duckdb
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw"
OUT = ROOT / "outputs"


def _rows_in_csv(path: Path) -> int:
    return len(pd.read_csv(path, dtype=str, encoding="utf-8-sig"))


def _check(results, check_id, kind, description, expected, actual, tolerance=0, note="", warn_tolerance=None):
    """PASS within tolerance; WARN within a documented, explained variance; else FAIL."""
    diff = None if expected is None or actual is None else actual - expected
    if diff is not None and abs(diff) <= tolerance:
        status = "PASS"
    elif diff is not None and warn_tolerance is not None and abs(diff) <= warn_tolerance:
        status = "WARN"
    else:
        status = "FAIL"
    results.append(dict(check_id=check_id, kind=kind, description=description,
                        expected=expected, actual=actual, difference=diff,
                        tolerance=tolerance, status=status, note=note))


def run(con: duckdb.DuckDBPyConnection) -> int:
    r: list[dict] = []
    q = lambda sql: con.execute(sql).fetchall()  # noqa: E731

    # --- load completeness -------------------------------------------------
    for prefix, table in [("PB_", "raw_plan_budgets"), ("UT_", "raw_utilisation")]:
        for f in sorted(RAW.glob(prefix + "*")):
            exp = _rows_in_csv(f) if f.suffix == ".csv" else len(pd.read_excel(f))
            act = q(f"SELECT count(*) FROM {table} WHERE source_file = '{f.name}'")[0][0]
            _check(r, f"L-{f.stem}", "load", f"rows loaded from {f.name}", exp, act)
    _check(r, "L-SDA", "load", "rows loaded from SDA_participants.csv",
           _rows_in_csv(RAW / "SDA_participants.csv"), q("SELECT count(*) FROM raw_sda_participants")[0][0])

    # --- internal hierarchy ------------------------------------------------
    for d, nat, states in q("""
        SELECT n.report_date, n.participants, s.total
        FROM stg_plan_budgets n
        JOIN (SELECT report_date, sum(participants) total FROM stg_plan_budgets
              WHERE state <> 'ALL' AND district = 'ALL' AND disability_group = 'ALL'
                AND age_band = 'ALL' AND support_class = 'ALL' GROUP BY 1) s USING (report_date)
        WHERE n.state = 'ALL' AND n.district = 'ALL' AND n.disability_group = 'ALL'
          AND n.age_band = 'ALL' AND n.support_class = 'ALL' ORDER BY 1"""):
        _check(r, f"I-PB-states-{d}", "internal", f"{d}: sum of state active participants = national", nat, states)

    # Districts -> state: masked district cells are missing from the sum, so the
    # tolerance is the sum of their published upper bounds (one-sided).
    for d, st, total, dsum, n_masked, mask_bound in q("""
        SELECT s.report_date, s.state, s.participants, coalesce(sum(d.participants_filled), 0),
               count(*) FILTER (WHERE d.participants_filled IS NULL),
               coalesce(sum(d.participants_upper) FILTER (WHERE d.participants_filled IS NULL), 0)
        FROM stg_plan_budgets s
        JOIN stg_plan_budgets d
          ON d.report_date = s.report_date AND d.state = s.state AND d.district <> 'ALL'
         AND d.disability_group = 'ALL' AND d.age_band = 'ALL' AND d.support_class = 'ALL'
        WHERE s.district = 'ALL' AND s.state NOT IN ('ALL', 'State_Missing', 'OT')
          AND s.disability_group = 'ALL' AND s.age_band = 'ALL' AND s.support_class = 'ALL'
        GROUP BY ALL ORDER BY 1, 2"""):
        _check(r, f"I-PB-districts-{st}-{d}", "internal",
               f"{d} {st}: sum of district participants = state total", total, dsum,
               tolerance=mask_bound, warn_tolerance=mask_bound + 200,
               note=f"{n_masked} masked district cell(s), bound {mask_bound}; filled cells use the "
                    "district total (state=ALL), which can include a few cross-border participants")

    bad = q("""SELECT count(*) FROM stg_plan_budgets
               WHERE participants_source LIKE 'district total%' AND participants_filled > participants_upper""")[0][0]
    _check(r, "I-PB-complementary-mask", "internal",
           "complementary-mask fallback never exceeds the published mask bound", 0, bad)

    for d, n in q("""SELECT p.report_date, count(u.report_date) FROM dim_period p
                     LEFT JOIN mart_util_national u ON u.report_date = p.report_date AND u.support_class = 'ALL'
                     GROUP BY 1 ORDER BY 1"""):
        _check(r, f"I-UT-national-{d}", "internal", f"{d}: national utilisation row present", 1, n)
    _check(r, "I-UT-range", "internal", "utilisation rates within [0, 1.5]", 0,
           q("SELECT count(*) FROM stg_utilisation WHERE utilisation < 0 OR utilisation > 1.5")[0][0])
    _check(r, "I-SDA-unique", "internal", "SDA rows unique on date x state x district x status", 0,
           q("""SELECT count(*) FROM (SELECT report_date, state, district_source, sda_status, count(*) c
                FROM stg_sda_participants GROUP BY ALL HAVING c > 1)""")[0][0])
    _check(r, "I-SDA-quarters", "internal", "SDA file covers the same 8 quarters as budgets",
           8, q("SELECT count(DISTINCT report_date) FROM stg_sda_participants s JOIN dim_period USING (report_date)")[0][0])

    # --- cross-source: CSV vs NDIA SDA supplement (30 June 2026) -----------
    p1 = con.execute("""
        SELECT region,
               TRY_CAST("Participants with SDA in use" AS INTEGER) AS in_use,
               TRY_CAST("Participants SDA eligible, not yet using SDA" AS INTEGER) AS not_using,
               TRY_CAST("Number of active participants" AS INTEGER) AS active
        FROM raw_sda_p1_district""").df()
    csv = con.execute("""
        SELECT state, district_source, sda_status, participants
        FROM stg_sda_participants WHERE report_date = DATE '2026-06-30'""").df()
    tot = p1[p1.region == "Total"].iloc[0]
    for status, col in [("SDA in use", "in_use"), ("SDA eligible - not yet using", "not_using")]:
        all_published = csv[csv.sda_status == status].participants.sum()
        _check(r, f"X-SDA-national-{col}", "cross-source",
               f"30-Jun-2026 national '{status}': CSV published cells vs supplement Table P.1 total",
               int(tot[col]), int(all_published),
               tolerance=10 * int(csv[(csv.sda_status == status)].participants.isna().sum()),
               note="CSV masks small districts ('<11'); tolerance = 10 x masked cells")

    # district-by-district. Label differences between the two NDIA products:
    #   supplement 'TAS - North' = CSV 'TAS North'; supplement 'ACT' state row = CSV district 'ACT';
    #   supplement '<STATE> - Other' = CSV '<STATE> - Other'.
    # The products also mask differently (supplement '<80' where CSV shows 77), so
    # values are compared only where both publish a number.
    p1d = p1[p1.region.str.contains(" - ", regex=False) | (p1.region == "ACT")].copy()
    p1d["state"] = p1d.region.str.split(" - ").str[0]
    p1d["district_source"] = p1d.region.str.split(" - ", n=1).str[1].fillna(p1d.region)
    p1d.loc[p1d.district_source == "Other", "district_source"] = p1d.state + " - Other"
    tas = (p1d.state == "TAS") & ~p1d.district_source.str.endswith("Other")
    p1d.loc[tas, "district_source"] = "TAS " + p1d.loc[tas, "district_source"]
    m = csv.pivot_table(index=["state", "district_source"], columns="sda_status",
                        values="participants", aggfunc="first").reset_index()
    m = p1d.merge(m, on=["state", "district_source"], how="outer", indicator=True)
    both = m[m._merge == "both"]
    pairs = list(zip(both.in_use, both["SDA in use"])) + list(zip(both.not_using, both["SDA eligible - not yet using"]))
    compared = [(int(a), int(b)) for a, b in pairs if pd.notna(a) and pd.notna(b)]
    mism = [abs(a - b) for a, b in compared if a != b]
    _check(r, "X-SDA-district-values", "cross-source",
           f"30-Jun-2026 district SDA counts: CSV vs supplement Table P.1 ({len(compared)} cells both unmasked)",
           0, len(mism), warn_tolerance=3,
           note=f"max absolute difference {max(mism) if mism else 0}; separate extracts")
    _check(r, "X-SDA-district-coverage", "cross-source",
           "every CSV district exists in supplement Table P.1", 0, int((m._merge == "right_only").sum()),
           note=f"{int((m._merge == 'left_only').sum())} supplement-only rows are zero/masked districts the CSV omits")

    # active participants by state: budgets CSV vs supplement Table P.1
    pb_states = dict(q("""SELECT state, participants FROM stg_plan_budgets
                          WHERE report_date = DATE '2026-06-30' AND district = 'ALL' AND state <> 'ALL'
                            AND disability_group = 'ALL' AND age_band = 'ALL' AND support_class = 'ALL'"""))
    for _, row in p1[p1.region.isin(["NSW", "VIC", "QLD", "WA", "SA", "TAS", "ACT", "NT"])].iterrows():
        _check(r, f"X-active-{row.region}", "cross-source",
               f"30-Jun-2026 {row.region} active participants: budgets CSV vs SDA supplement P.1",
               int(row.active), pb_states.get(row.region), warn_tolerance=5,
               note="supplement and CSV are separate NDIA extracts; differences of a few participants are WARN")

    # SA4 rows add up to state rows in Table P.9
    sa4 = con.execute("SELECT * FROM mart_sda_sa4_context").df()
    for st, g in sa4.groupby("state"):
        if st in ("Missing", "OT"):
            continue
        state_row = g[~g.is_sa4]
        if state_row.empty:
            continue
        _check(r, f"I-SA4-{st}", "internal", f"Table P.9: SA4 participants with SDA need sum to {st}",
               int(state_row.with_need.iloc[0]), int(g[g.is_sa4].with_need.sum()))

    df = pd.DataFrame(r)
    OUT.mkdir(exist_ok=True)
    df.to_csv(OUT / "reconciliation.csv", index=False)
    con.register("rec", df)
    con.execute("CREATE OR REPLACE TABLE qa_reconciliation AS SELECT * FROM rec")
    con.unregister("rec")
    fails = int((df.status == "FAIL").sum())
    print(f"reconciliation: {len(df)} checks, {fails} failed")
    return fails


if __name__ == "__main__":
    c = duckdb.connect(str(ROOT / "data" / "ndis.duckdb"))
    run(c)
    print(pd.read_csv(OUT / "reconciliation.csv").query("status == 'FAIL'").to_string())
