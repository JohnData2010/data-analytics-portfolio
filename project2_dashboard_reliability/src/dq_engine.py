"""Data Quality Engine.

Each rule counts affected rows AND measures what those rows do to the KPIs they
touch: KPI on all rows vs KPI with the flagged rows excluded. A rule with many
hits and no KPI impact is housekeeping; a rule with few hits and a large impact
is a reporting risk.
"""
from datetime import date

import duckdb
import numpy as np
import pandas as pd

# rule id -> (dataset, flag column or SQL predicate, description, severity, KPIs touched)
RULES = [
    ("DQ01", "A", "dq_invalid_received", "Date received missing or unparseable", "critical", ["K01", "K03", "K05"]),
    ("DQ01", "B", "dq_invalid_received", "Date opened missing or unparseable", "critical", ["K01", "K03", "K05"]),
    ("DQ02", "A", "dq_invalid_completed", "Date completed present but unparseable", "critical", ["K01", "K02"]),
    ("DQ02", "B", "dq_invalid_completed", "Date closed present but unparseable", "critical", ["K01", "K02"]),
    ("DQ03", "A", "dq_completed_before_received", "Completed before received", "critical", ["K01", "K02"]),
    ("DQ03", "B", "dq_completed_before_received", "Closed before opened", "critical", ["K01", "K02"]),
    ("DQ04", "A", "dq_future_date", "Date after the extract date", "high", ["K04"]),
    ("DQ04", "B", "dq_future_date", "Date after the extract date", "high", ["K04"]),
    ("DQ05", "A", "dq_closed_no_date", "Status closed but no completion date", "high", ["K01", "K02"]),
    ("DQ05", "B", "dq_closed_no_date", "Status closed but no close date", "high", ["K01", "K02"]),
    ("DQ06", "A", "dq_open_with_date", "Status open but has a completion date", "medium", ["K05", "K04"]),
    ("DQ06", "B", "dq_open_with_date", "Status open but has a close date", "medium", ["K05", "K04"]),
    ("DQ07", "A", "dq_days_field_on_open", "days_to_complete populated on open request (holds age, not completion time)", "high", ["K07"]),
    ("DQ08", "A", "dq_days_field_mismatch", "days_to_complete differs from calendar days (same-day = 1 convention)", "medium", ["K01"]),
    ("DQ09", "A", "dq_missing_suburb", "Suburb missing", "medium", ["suburb KPIs"]),
    ("DQ09", "B", "dq_missing_suburb", "Suburb missing", "medium", ["suburb KPIs"]),
    ("DQ10", "B", "dq_suburb_variant", "Suburb spelling variant (e.g. 'Melb CBD', lower case)", "low", ["suburb KPIs"]),
    ("DQ11", "B", "dq_suburb_outside_com", "Suburb outside City of Melbourne municipality", "low", ["suburb KPIs"]),
    ("DQ12", "B", "is_duplicate", "Closed as Duplicate (not a resolved service request)", "medium", ["K01", "K02", "K05"]),
]


def _kpis(df: pd.DataFrame, days_col: str) -> dict:
    closed = df[df.is_closed]
    return {
        "K01": closed[days_col].median(),
        "K02": closed[days_col].quantile(0.9),
        "K05": (~df.is_closed).mean(),
        "K04": df.loc[~df.is_closed, "age_at_extract"].median(),
        "K07": df["days_field"].median() if "days_field" in df else np.nan,
    }


def run(con: duckdb.DuckDBPyConnection) -> pd.DataFrame:
    frames = {"A": con.execute("SELECT * FROM stg_requests_a").df(),
              "B": con.execute("SELECT * FROM stg_requests_b").df()}
    days = {"A": "days_field", "B": "days_to_close"}
    rows = []
    for rid, ds, col, desc, sev, kpis in RULES:
        df = frames[ds]
        flag = df[col].fillna(False).astype(bool)
        n = int(flag.sum())
        base, excl = _kpis(df, days[ds]), _kpis(df[~flag], days[ds])
        impacts = []
        for k in kpis:
            if k in base and pd.notna(base[k]) and pd.notna(excl[k]):
                d = excl[k] - base[k]
                impacts.append(f"{k}: {base[k]:.3g} -> {excl[k]:.3g} ({d:+.3g})")
        rows.append(dict(rule_id=rid, dataset=ds, check=desc, severity=sev, rows_flagged=n,
                         rate=n / len(df), kpis_touched=", ".join(kpis),
                         kpi_impact_if_excluded="; ".join(impacts) if impacts else "no numeric KPI impact",
                         status="PASS" if n == 0 else ("FAIL" if sev == "critical" else "WARN")))

    # dataset-level checks
    a, b = frames["A"], frames["B"]
    man = con.execute("SELECT dataset, portal_modified, portal_records_count, rows FROM gov_source_manifest").df().set_index("dataset")
    today = date.today()
    for ds, df, dcol in [("A", a, "date_received"), ("B", b, "date_opened")]:
        ext = pd.Timestamp(df.extract_date.iloc[0]).date()
        rows.append(dict(rule_id="DQ13", dataset=ds, check=f"Freshness: latest record {ext}, {(today - ext).days} days before this run",
                         severity="high", rows_flagged=len(df), rate=1.0, kpis_touched="all",
                         kpi_impact_if_excluded="KPIs describe the past, not current service performance",
                         status="WARN" if (today - ext).days > 90 else "PASS"))
        rows.append(dict(rule_id="DQ14", dataset=ds, check="Row count matches portal record count",
                         severity="critical", rows_flagged=abs(int(man.loc[ds, "rows"]) - int(man.loc[ds, "portal_records_count"])),
                         rate=0.0, kpis_touched="all", kpi_impact_if_excluded="",
                         status="PASS" if int(man.loc[ds, "rows"]) == int(man.loc[ds, "portal_records_count"]) else "FAIL"))
        dup = int(df.drop(columns=["source_row"]).duplicated().sum())
        rows.append(dict(rule_id="DQ15", dataset=ds, check="Exact duplicate rows (no request ID exists to test true duplicates)",
                         severity="medium", rows_flagged=dup, rate=dup / len(df), kpis_touched="counts",
                         kpi_impact_if_excluded="", status="PASS" if dup == 0 else "WARN"))
        months = pd.to_datetime(df[dcol]).dt.to_period("M").value_counts().sort_index()
        med = months.median()
        partial = months[months < 0.5 * med]
        rows.append(dict(rule_id="DQ16", dataset=ds,
                         check=f"Partial months at the edges (< 50% of median monthly volume): {', '.join(str(p) for p in partial.index) or 'none'}",
                         severity="medium", rows_flagged=int(partial.sum()), rate=partial.sum() / len(df),
                         kpis_touched="monthly trend KPIs", kpi_impact_if_excluded="exclude from month-on-month comparisons",
                         status="WARN" if len(partial) else "PASS"))
    tz = int((b.intake_month != b.intake_month_utc).sum())
    rows.append(dict(rule_id="DQ17", dataset="B", check="Requests whose month differs between UTC and Melbourne time",
                     severity="low", rows_flagged=tz, rate=tz / len(b), kpis_touched="monthly volumes, K03",
                     kpi_impact_if_excluded="handled: months assigned in local time", status="PASS"))
    out = pd.DataFrame(rows)
    con.register("dq", out)
    con.execute("CREATE OR REPLACE TABLE qa_dq_results AS SELECT * FROM dq")
    con.unregister("dq")
    print("dq engine:", out.status.value_counts().to_dict())
    return out
