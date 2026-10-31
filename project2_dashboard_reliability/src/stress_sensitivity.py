"""KPI Stress Testing and Sensitivity Analysis.

Stress test: for every service group, compute several defensible service-performance
KPIs, rank groups on each, and record where the rankings disagree.

Sensitivity: rerun the headline KPIs and the headline *conclusions* under alternative
reasonable assumptions (day-count convention, reporting period, exclusions,
timezone, duplicates) and flag any conclusion that changes.
"""
import itertools

import duckdb
import numpy as np
import pandas as pd

from kpi_engine import MIN_GROUP, _save, kpi_values

STRESS_KPIS = {
    # kpi key -> (label, higher_is_better)
    "K01": ("Median days (completed)", False),
    "K02": ("P90 days (completed)", False),
    "K03": ("Closed within 30 days, intake cohort", True),
    "K04_median_age": ("Median age of open requests", False),
    "K05": ("Open share", False),
    "K06": ("KM median days (open = censored)", False),
}


def stress_test(con, df: pd.DataFrame):
    rows = []
    for ds in ["A", "B"]:
        d = df[df.dataset == ds]
        groups = d.service_group.value_counts()
        for g in groups[groups >= MIN_GROUP].index:
            v = kpi_values(d[d.service_group == g])
            for k, (label, hib) in STRESS_KPIS.items():
                rows.append(dict(dataset=ds, service_group=g, requests=int(groups[g]), kpi=k, kpi_label=label,
                                 value=v[k], higher_is_better=hib))
    st = pd.DataFrame(rows)
    # rank 1 = best performer on that KPI; groups with no open requests get no ageing rank
    st["rank"] = st.groupby(["dataset", "kpi"]).apply(
        lambda g: g.value.rank(ascending=not g.higher_is_better.iloc[0], method="min"), include_groups=False
    ).reset_index(level=[0, 1], drop=True)
    st["groups_ranked"] = st.groupby(["dataset", "kpi"]).value.transform("count")
    _save(con, "stress_kpi_values", st)

    # pairwise reversals relative to the headline KPI (K01)
    rev = []
    for ds, d in st.groupby("dataset"):
        w = d.pivot(index="service_group", columns="kpi", values="value")
        for g1, g2 in itertools.combinations(w.index, 2):
            if w.loc[g1, "K01"] == w.loc[g2, "K01"]:
                continue
            faster, slower = (g1, g2) if w.loc[g1, "K01"] < w.loc[g2, "K01"] else (g2, g1)
            for k, (label, hib) in STRESS_KPIS.items():
                if k == "K01" or pd.isna(w.loc[faster, k]) or pd.isna(w.loc[slower, k]):
                    continue
                worse = w.loc[faster, k] < w.loc[slower, k] if hib else w.loc[faster, k] > w.loc[slower, k]
                if worse:
                    rev.append(dict(dataset=ds, faster_on_median=faster, slower_on_median=slower, kpi=k,
                                    kpi_label=label, faster_value=w.loc[faster, k], slower_value=w.loc[slower, k],
                                    median_faster=w.loc[faster, "K01"], median_slower=w.loc[slower, "K01"]))
    rev = pd.DataFrame(rev)
    _save(con, "stress_reversals", rev)

    # rank agreement matrix (Spearman on ranks)
    corr = []
    for ds, d in st.groupby("dataset"):
        r = d.pivot(index="service_group", columns="kpi", values="rank")
        c = r.corr(method="spearman")
        for a in c.index:
            for b in c.columns:
                corr.append(dict(dataset=ds, kpi_a=a, kpi_b=b, spearman=c.loc[a, b], groups=len(r)))
    corr = pd.DataFrame(corr)
    _save(con, "stress_rank_agreement", corr)
    return st, rev, corr


# --------------------------------------------------------------------------- sensitivity
def _headline(d: pd.DataFrame, days_col="completion_days") -> dict:
    v = kpi_values(d, days_col)
    groups = d.service_group.value_counts()
    big = groups[groups >= MIN_GROUP].index
    per = {g: kpi_values(d[d.service_group == g], days_col) for g in big}
    k01 = pd.Series({g: per[g]["K01"] for g in big})
    k06 = pd.Series({g: per[g]["K06"] for g in big})
    return dict(requests=len(d), K01=v["K01"], K02=v["K02"], K03=v["K03"], K05=v["K05"], K06=v["K06"],
                slowest_by_median=k01.idxmax(), slowest_by_km=k06.idxmax(),
                fastest_by_median=k01.idxmin())


def sensitivity(con, df: pd.DataFrame) -> pd.DataFrame:
    a = df[df.dataset == "A"].copy()
    b = df[df.dataset == "B"].copy()
    a_cal = a.copy()
    a_cal["completion_days"] = a_cal.completion_days_calendar
    a_cal["duration"] = np.where(a_cal.is_closed, a_cal.completion_days, a_cal.age_at_extract)
    in_period = lambda d, s, e: d[(d.received >= s) & (d.received <= e)]  # noqa: E731
    b_utc = b.copy()
    b_utc["intake_month"] = pd.to_datetime(b_utc.intake_month_utc)

    scenarios = [
        ("A", "A0 baseline: published field, closed only, full extract", a, "baseline"),
        ("A", "A1 calendar days (same-day = 0) instead of published field", a_cal, "day-count convention"),
        ("A", "A2 reporting period: calendar 2015", in_period(a, "2015-01-01", "2015-12-31"), "reporting period"),
        ("A", "A3 reporting period: Jan-Oct 2016", in_period(a, "2016-01-01", "2016-10-31"), "reporting period"),
        ("A", "A4 reporting period: FY 2015-16", in_period(a, "2015-07-01", "2016-06-30"), "reporting period"),
        ("A", "A5 exclude partial edge months (Nov-2014, Nov-2016)", in_period(a, "2014-12-01", "2016-10-31"), "reporting period"),
        ("A", "A6 most recent 3 months only (Aug-Oct 2016)", in_period(a, "2016-08-01", "2016-10-31"), "reporting period"),
        ("A", "A7 exclude rows with missing suburb", a[a.suburb.notna()], "missing-data exclusion"),
        ("B", "B0 baseline: Melbourne time, duplicates count as closed", b, "baseline"),
        ("B", "B1 exclude 'Closed as Duplicate'", b[~b.is_duplicate], "exclusion"),
        ("B", "B2 exclude 'Infringement Review - Parking' (48% of rows)", b[b.service_group != "Infringement Review - Parking"], "scope"),
        ("B", "B3 reporting period: Q1 2018", in_period(b, "2018-01-01", "2018-03-31"), "reporting period"),
        ("B", "B4 reporting period: Q3 2018", in_period(b, "2018-07-01", "2018-09-30"), "reporting period"),
        ("B", "B5 Q3 2018 cut in UTC months", b_utc[(b_utc.intake_month >= "2018-07-01") & (b_utc.intake_month <= "2018-09-01")], "timezone"),
        ("B", "B6 exclude rows with missing suburb", b[b.suburb.notna()], "missing-data exclusion"),
    ]
    rows = []
    base = {}
    for ds, name, d, kind in scenarios:
        h = _headline(d)
        if kind == "baseline":
            base[ds] = h
        bh = base[ds]
        rows.append(dict(dataset=ds, scenario=name, assumption_type=kind, **h,
                         d_K01=h["K01"] - bh["K01"], d_K06=h["K06"] - bh["K06"], d_K03=h["K03"] - bh["K03"],
                         slowest_changed=h["slowest_by_median"] != bh["slowest_by_median"],
                         slowest_by_km_changed=h["slowest_by_km"] != bh["slowest_by_km"],
                         median_vs_km_disagree=h["slowest_by_median"] != h["slowest_by_km"]))
    out = pd.DataFrame(rows)
    _save(con, "sensitivity_scenarios", out)

    # trend conclusion: is 2016 better or worse than 2015? (A) / Q3 vs Q1 (B)
    trend = []
    for ds, (p0, s0, e0), (p1, s1, e1) in [
        ("A", ("CY2015", "2015-01-01", "2015-12-31"), ("Jan-Oct 2016", "2016-01-01", "2016-10-31")),
        ("B", ("Q1 2018", "2018-01-01", "2018-03-31"), ("Q3 2018", "2018-07-01", "2018-09-30")),
    ]:
        d = df[df.dataset == ds]
        v0, v1 = kpi_values(in_period(d, s0, e0)), kpi_values(in_period(d, s1, e1))
        for k, (label, hib) in STRESS_KPIS.items():
            delta = v1[k] - v0[k]
            better = (delta > 0) if hib else (delta < 0)
            trend.append(dict(dataset=ds, kpi=k, kpi_label=label, period_from=p0, period_to=p1,
                              value_from=v0[k], value_to=v1[k], change=delta,
                              direction="improved" if better and delta != 0 else ("no change" if delta == 0 else "worsened")))
    trend = pd.DataFrame(trend)
    _save(con, "sensitivity_trend", trend)
    return out


def run(con: duckdb.DuckDBPyConnection, df: pd.DataFrame) -> None:
    st, rev, corr = stress_test(con, df)
    sens = sensitivity(con, df)
    print(f"stress test: {st.service_group.nunique()} groups, {len(rev)} pairwise reversals vs median")
    print(f"sensitivity: {len(sens)} scenarios, {int(sens.slowest_changed.sum())} change the slowest-group conclusion")
