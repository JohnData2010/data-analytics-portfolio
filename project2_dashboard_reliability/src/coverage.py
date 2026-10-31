"""Coverage Assessment and backlog-reconstruction limits.

Answers: what period, system and population does each dataset represent, and what
can it NOT be used for?
"""
import duckdb
import numpy as np
import pandas as pd

from kpi_engine import _save

COMPARABILITY = {
    # can a KPI be compared across intake periods of different age?
    "K01": "biased for recent periods: slow requests not yet closed are missing",
    "K02": "biased for recent periods: slow requests not yet closed are missing",
    "K03": "comparable: fixed 30-day window, only cohorts with full follow-up",
    "K04_median_age": "not comparable: older intake periods can only have older open requests",
    "K05": "not comparable: recent intake has had less time to close",
    "K06": "comparable with assumption: censoring handled, assumes non-informative censoring",
}


def coverage(con, df: pd.DataFrame) -> pd.DataFrame:
    rows = []
    man = con.execute("SELECT * FROM gov_source_manifest").df().set_index("dataset")
    for ds, d in df.groupby("dataset"):
        rows.append(dict(dataset=ds, aspect="Period covered",
                         finding=f"{d.received.min():%d %b %Y} to {d.received.max():%d %b %Y} "
                                 f"({d.intake_month.nunique()} intake months); extract date {d.extract_date.iloc[0]:%d %b %Y}",
                         implication="KPIs describe this window only"))
        rows.append(dict(dataset=ds, aspect="Source system",
                         finding=" ".join(str(man.loc[ds, "publisher_description"]).split())[:300],
                         implication=("Subset of council requests (one of several systems): "
                                      "must not be presented as all council activity") if ds == "B"
                         else "Council request system; confidential requests removed by publisher"))
        miss = d.groupby("service_group").suburb.apply(lambda s: s.isna().mean()).sort_values(ascending=False)
        rows.append(dict(dataset=ds, aspect="Suburb missingness by service group",
                         finding="; ".join(f"{g}: {m:.0%}" for g, m in miss.head(5).items()),
                         implication="Missing not at random: excluding missing-suburb rows changes the service mix "
                                     "and therefore every KPI (see sensitivity A7/B6)"))
    a_end, b_start = df[df.dataset == "A"].received.max(), df[df.dataset == "B"].received.min()
    rows.append(dict(dataset="A+B", aspect="Overlap between datasets",
                     finding=f"No time overlap: A ends {a_end:%b %Y}, B starts {b_start:%b %Y}; "
                             "different status vocabularies (5 vs 10 values) and taxonomies (6 categories vs 14 service types)",
                     implication="Datasets cannot be merged, trended together or used to estimate B's share of all requests"))
    b = df[df.dataset == "B"]
    ch = con.execute("SELECT channel_group, count(*) n FROM stg_requests_b GROUP BY 1").df().set_index("channel_group").n
    rows.append(dict(dataset="B", aspect="Channel coverage",
                     finding=f"Digital {ch.get('Digital', 0) / ch.sum():.0%}, assisted {ch.get('Assisted', 0) / ch.sum():.0%}, "
                             f"council-initiated {ch.get('Council-initiated', 0) / ch.sum():.0%}; "
                             f"'Infringement Review - Parking' is {(b.service_group == 'Infringement Review - Parking').mean():.0%} of rows",
                     implication="Digital share describes Salesforce-handled requests only, and is dominated by parking reviews"))
    out = pd.DataFrame(rows)
    _save(con, "coverage_assessment", out)
    return out


def backlog(con, df: pd.DataFrame) -> pd.DataFrame:
    """Reconstruct month-end open backlog from received/completed dates and flag
    where the reconstruction cannot be trusted."""
    rows = []
    for ds, d in df.groupby("dataset"):
        start = d.received.min()
        ext = d.extract_date.iloc[0]
        p99 = d.loc[d.is_closed, "completion_days"].quantile(0.99)
        warm_up_end = start + pd.Timedelta(days=float(p99))
        # open requests are handled by ~is_closed below; the fill value only avoids NaT arithmetic
        completed = d.received + pd.to_timedelta(d.completion_days_calendar.astype(float).fillna(0), unit="D")
        for m in pd.date_range(start, ext, freq="ME"):
            open_at = ((d.received <= m) & (~d.is_closed | (completed > m))).sum()
            rows.append(dict(dataset=ds, month_end=m, reconstructed_open=int(open_at),
                             reliable=m >= warm_up_end,
                             reason="" if m >= warm_up_end else
                             f"warm-up: requests received before {start:%d %b %Y} are not in the extract; "
                             f"99% of closed requests closed within {p99:.0f} days (a lower bound)"))
    out = pd.DataFrame(rows)
    _save(con, "backlog_reconstruction", out)
    limits = pd.DataFrame([
        dict(limitation="Left truncation", detail="Requests received before the first date in the extract, but still open, are absent; backlog is understated until the warm-up period ends."),
        dict(limitation="No status history", detail="Only the current status is published. Reassignments, reopenings and time spent in each status cannot be reconstructed."),
        dict(limitation="Removed records", detail="Confidential requests were removed by the publisher (A); B is one system of several. Missing records cannot be counted."),
        dict(limitation="Completion definition", detail="A's metadata says days_to_complete excludes time referred to other authorities, but every closed row equals calendar days (same-day = 1); either no exclusion was applied or it is not visible. Paused time cannot be recovered."),
        dict(limitation="Single extract", detail="One snapshot per dataset. A month-end backlog derived from it is an estimate, not a recorded backlog; it cannot be reconciled to any operational backlog report."),
    ])
    b = con.execute("""SELECT count(*) FILTER (WHERE status_group = 'Open') open_n,
                              count(*) FILTER (WHERE status_raw = 'New') new_n FROM stg_requests_b""").fetchall()[0]
    bl = out[out.dataset == "B"].reconstructed_open
    limits.loc[len(limits)] = dict(
        limitation="Accumulating open records (B)",
        detail=f"Reconstructed open requests in B rise from {bl.iloc[0]:,} to {bl.iloc[-1]:,} in nine months; "
               f"{b[1]:,} of {b[0]:,} open records have status 'New'. The data cannot distinguish a genuine backlog "
               "from requests resolved elsewhere but never closed in Salesforce: requires investigation before any backlog KPI is published.")
    _save(con, "backlog_limitations", limits)
    return out


def run(con: duckdb.DuckDBPyConnection, df: pd.DataFrame) -> None:
    cov = coverage(con, df)
    bl = backlog(con, df)
    comp = pd.DataFrame([dict(kpi=k, comparability_across_periods=v) for k, v in COMPARABILITY.items()])
    _save(con, "kpi_period_comparability", comp)
    print(f"coverage: {len(cov)} findings; backlog months {len(bl)} ({int(bl.reliable.sum())} reliable)")
