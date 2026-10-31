"""KPI Integrity Engine.

Two separate questions per KPI:
  1. Computed correctly?  Python recomputes each SQL KPI independently; they must agree.
  2. Meaningful for the management question?  Rule-based, using how much of the
     population the KPI sees and how far it sits from a censoring-aware estimate.
"""
import json
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
KPI_DEFS = json.loads((ROOT / "config" / "kpi_definitions.json").read_text(encoding="utf-8"))
MIN_GROUP = 200


def load_requests(con) -> pd.DataFrame:
    """One row per request with a uniform duration/event pair for survival analysis."""
    a = con.execute("""
        SELECT 'A' AS dataset, category AS service_group, is_closed, FALSE AS is_duplicate,
               date_received AS received, intake_month, suburb, days_field,
               CASE WHEN is_closed THEN days_field END AS completion_days,
               CASE WHEN is_closed THEN days_calendar END AS completion_days_calendar,
               age_at_extract, extract_date
        FROM stg_requests_a""").df()
    b = con.execute("""
        SELECT 'B' AS dataset, service_type AS service_group, is_closed, is_duplicate,
               date_opened AS received, intake_month, intake_month_utc, suburb, NULL AS days_field,
               CASE WHEN is_closed THEN days_to_close END AS completion_days,
               CASE WHEN is_closed THEN date_closed - date_opened END AS completion_days_calendar,
               age_at_extract, extract_date
        FROM stg_requests_b""").df()
    df = pd.concat([a, b], ignore_index=True)
    df["received"] = pd.to_datetime(df.received)
    df["intake_month"] = pd.to_datetime(df.intake_month)
    df["extract_date"] = pd.to_datetime(df.extract_date)
    # K03 cohorts need 30 days of follow-up after the end of the intake month (right-censoring)
    df["full_window"] = (df.extract_date - (df.intake_month + pd.offsets.MonthEnd(0))).dt.days >= 30
    df["duration"] = np.where(df.is_closed, df.completion_days, df.age_at_extract).astype(float)
    df["event"] = df.is_closed.astype(int)
    return df


def kaplan_meier(duration: pd.Series, event: pd.Series) -> pd.DataFrame:
    """Product-limit estimate of S(t) = P(still open after t days)."""
    d = pd.DataFrame({"t": duration.values, "e": event.values}).dropna()
    g = d.groupby("t").agg(events=("e", "sum"), n=("e", "size")).sort_index()
    at_risk = len(d) - g.n.cumsum().shift(fill_value=0)
    s = (1 - g.events / at_risk).cumprod()
    return pd.DataFrame({"t": g.index, "survival": s.values, "at_risk": at_risk.values})


def km_quantile(km: pd.DataFrame, q: float) -> float:
    """Time by which a share q of requests is completed (NaN if never reached in follow-up)."""
    hit = km[km.survival <= 1 - q]
    return float(hit.t.iloc[0]) if len(hit) else np.nan


def km_closed_by(km: pd.DataFrame, t: float) -> float:
    s = km[km.t <= t].survival
    return float(1 - s.iloc[-1]) if len(s) else 0.0


def kpi_values(df: pd.DataFrame, days_col: str = "completion_days") -> dict:
    closed = df[df.is_closed]
    opened = df[~df.is_closed]
    cohort = df[df.full_window]
    km = kaplan_meier(df.duration, df.event)
    return {
        "requests": len(df),
        "K01": closed[days_col].median(),
        "K02": closed[days_col].quantile(0.9),
        "K03": (cohort.is_closed & (cohort[days_col] <= 30)).mean() if len(cohort) else np.nan,
        "K04_median_age": opened.age_at_extract.median(),
        "K04_share_over_90d": (opened.age_at_extract > 90).mean() if len(opened) else np.nan,
        "K05": (~df.is_closed).mean(),
        "K06": km_quantile(km, 0.5),
        "K06_p90": km_quantile(km, 0.9),
        "K06_closed_by_30d": km_closed_by(km, 30),
        "K07": df.days_field.median() if df.dataset.iloc[0] == "A" else np.nan,
    }


def integrity(con, df: pd.DataFrame) -> pd.DataFrame:
    sql = con.execute("SELECT * FROM kpi_sql_summary").df()
    rows = []
    for _, s in sql.iterrows():
        sub = df[df.dataset == s.dataset]
        if s.service_group != "(all)":
            sub = sub[sub.service_group == s.service_group]
        py = kpi_values(sub)
        for k, sql_col, py_key in [("K01", "k01_median_days", "K01"), ("K02", "k02_p90_days", "K02"),
                                   ("K04", "k04_open_median_age", "K04_median_age"), ("K05", "k05_open_share", "K05")]:
            a, b = s[sql_col], py[py_key]
            same = (pd.isna(a) and pd.isna(b)) or (pd.notna(a) and pd.notna(b) and abs(a - b) < 1e-6)
            rows.append(dict(dataset=s.dataset, service_group=s.service_group, kpi=k,
                             value_sql=a, value_python=b, computed_correctly="PASS" if same else "FAIL"))
    out = pd.DataFrame(rows)
    _save(con, "kpi_integrity_recompute", out)
    return out


def meaningfulness(con, df: pd.DataFrame) -> pd.DataFrame:
    """Is the KPI fit for 'How long do residents wait for a request to be resolved?'"""
    rows = []
    defs = {k["id"]: k for k in KPI_DEFS["kpis"]}
    for ds in ["A", "B"]:
        v = kpi_values(df[df.dataset == ds])
        open_share = v["K05"]
        gap = v["K06"] - v["K01"]
        def verdict(kpi):
            if kpi == "K07":
                return ("Not meaningful", "mixes completion times of closed requests with ages of open requests")
            if kpi in ("K01", "K02"):
                if open_share < 0.05 and abs(gap) <= 1:
                    return ("Meaningful", f"only {open_share:.1%} open; censoring-aware median within {abs(gap):.1f} days")
                if open_share < 0.15:
                    return ("Partial", f"{open_share:.1%} open requests excluded; read with K04")
                return ("Not meaningful on its own",
                        f"{open_share:.1%} of requests are open and excluded (median age {v['K04_median_age']:.0f} days)")
            if kpi == "K03":
                return ("Meaningful", "cohort-based: open requests count as not closed; only cohorts with a full 30-day window")
            if kpi == "K04":
                return ("Meaningful", "directly describes the open backlog at extract; one snapshot only")
            if kpi == "K05":
                return ("Partial", "depends on the age mix of intake; compare like-aged cohorts (K03)")
            if kpi == "K06":
                p90 = "not reached in follow-up" if pd.isna(v["K06_p90"]) else f"{v['K06_p90']:.0f} days"
                return ("Meaningful (with assumption)", f"keeps open requests as censored; KM p90 {p90}; assumes non-informative censoring")
            return ("", "")
        for kpi in ["K01", "K02", "K03", "K04", "K05", "K06", "K07"]:
            if ds == "B" and kpi == "K07":
                continue
            val = v["K04_median_age"] if kpi == "K04" else v[kpi]
            m, why = verdict(kpi)
            rows.append(dict(dataset=ds, kpi=kpi, name=defs[kpi]["name"], value=val,
                             population=defs[kpi]["population"], blind_spot=defs[kpi]["blind_spot"],
                             meaningful=m, reason=why))
    out = pd.DataFrame(rows)
    _save(con, "kpi_meaningfulness", out)
    return out


def km_curves(con, df: pd.DataFrame) -> pd.DataFrame:
    """KM step curves (first 365 days) per dataset and per large service group."""
    parts = []
    for ds, d in df.groupby("dataset"):
        groups = d.service_group.value_counts()
        for g, sub in [("(all)", d)] + [(g, d[d.service_group == g]) for g in groups[groups >= MIN_GROUP].index]:
            km = kaplan_meier(sub.duration, sub.event)
            km = km[km.t <= 365].assign(dataset=ds, service_group=g, share_closed=lambda x: 1 - x.survival)
            parts.append(km)
    out = pd.concat(parts, ignore_index=True)
    _save(con, "km_curves", out)
    return out


def _save(con, name, df):
    con.register("tmp", df)
    con.execute(f"CREATE OR REPLACE TABLE {name} AS SELECT * FROM tmp")
    con.unregister("tmp")


def run(con: duckdb.DuckDBPyConnection) -> pd.DataFrame:
    df = load_requests(con)
    ig = integrity(con, df)
    mf = meaningfulness(con, df)
    km_curves(con, df)
    print(f"kpi engine: recompute {ig.computed_correctly.value_counts().to_dict()}")
    print(mf[["dataset", "kpi", "value", "meaningful"]].to_string())
    return df
