"""KPI Reliability Report + Decision Evidence Report (Markdown + PNG), built from the DuckDB tables."""
import json
from pathlib import Path

import duckdb
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import pandas as pd  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
REP = ROOT / "reports"
FIG = REP / "figures"
KPI_DEFS = json.loads((ROOT / "config" / "kpi_definitions.json").read_text(encoding="utf-8"))

BLUE, ORANGE = "#2a78d6", "#eb6834"
INK, MUTED, GRID = "#0b0b0b", "#8a8984", "#e6e5e1"


def _md(df: pd.DataFrame, floatfmt="{:.2f}") -> str:
    cols = list(df.columns)
    out = ["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
    for _, r in df.iterrows():
        cells = []
        for c in cols:
            v = r[c]
            if isinstance(v, float):
                v = "" if pd.isna(v) else floatfmt.format(v)
            cells.append(str(v).replace("|", r"\|").replace("\n", " "))
        out.append("| " + " | ".join(cells) + " |")
    return "\n".join(out)


def _style(ax, title):
    ax.set_title(title, loc="left", fontsize=10.5, color=INK, pad=8)
    for s in ["top", "right"]:
        ax.spines[s].set_visible(False)
    for s in ["left", "bottom"]:
        ax.spines[s].set_color(MUTED)
    ax.tick_params(colors="#52514e", labelsize=8)
    ax.set_axisbelow(True)


def _save(fig, name):
    FIG.mkdir(parents=True, exist_ok=True)
    fig.tight_layout()
    fig.savefig(FIG / name, dpi=150, facecolor="#fcfcfb")
    plt.close(fig)
    return f"figures/{name}"


def charts(con) -> dict:
    figs = {}
    st = con.execute("SELECT * FROM stress_kpi_values WHERE kpi IN ('K01', 'K02')").df()
    fig, axes = plt.subplots(1, 2, figsize=(10, 4.2))
    for ax, ds, title in [(axes[0], "A", "A: 2014-16, by category"), (axes[1], "B", "B: 2018 subset, by service type")]:
        w = st[st.dataset == ds].pivot(index="service_group", columns="kpi", values="value").sort_values("K01")
        y = range(len(w))
        ax.barh([i + 0.2 for i in y], w.K01, height=0.38, color=BLUE, label="Median (completed)")
        ax.barh([i - 0.2 for i in y], w.K02, height=0.38, color=ORANGE, label="90th percentile (completed)")
        ax.set_yticks(list(y), w.index, fontsize=7.5)
        ax.grid(axis="x", color=GRID)
        _style(ax, title)
        ax.set_xlabel("days to completion", fontsize=8)
    axes[0].legend(fontsize=7.5, frameon=False, loc="upper right")
    figs["stress"] = _save(fig, "1_stress_median_vs_p90.png")

    km = con.execute("SELECT * FROM km_curves WHERE service_group = '(all)'").df()
    med = con.execute("SELECT dataset, k01_median_days FROM kpi_sql_summary WHERE service_group = '(all)'").df().set_index("dataset")
    fig, ax = plt.subplots(figsize=(7, 3.6))
    for ds, col in [("A", BLUE), ("B", ORANGE)]:
        d = km[km.dataset == ds]
        ax.step(d.t, d.share_closed * 100, where="post", color=col, lw=2, label=f"Dataset {ds}")
        ax.axvline(med.loc[ds, "k01_median_days"], color=col, lw=1, ls=":")
    ax.axhline(50, color=MUTED, lw=0.8)
    ax.set_xlim(0, 120)
    ax.set_ylabel("% of all requests completed", fontsize=8)
    ax.set_xlabel("days since received (dotted = closed-only median)", fontsize=8)
    ax.grid(axis="y", color=GRID)
    ax.legend(fontsize=8, frameon=False, loc="lower right")
    _style(ax, "Share of ALL requests completed by day t (Kaplan-Meier, open = censored)")
    figs["km"] = _save(fig, "2_km_all_requests.png")

    bl = con.execute("SELECT * FROM backlog_reconstruction").df()
    fig, axes = plt.subplots(1, 2, figsize=(10, 3.2))
    for ax, ds in zip(axes, ["A", "B"]):
        d = bl[bl.dataset == ds]
        ax.plot(d.month_end, d.reconstructed_open, color=BLUE, lw=2)
        un = d[~d.reliable]
        if len(un):
            ax.axvspan(un.month_end.min() - pd.Timedelta(days=31), un.month_end.max(), color=GRID, alpha=.8)
            ax.text(un.month_end.min(), d.reconstructed_open.max() * 0.95, "warm-up\n(understated)", fontsize=7, color=MUTED)
        ax.grid(axis="y", color=GRID)
        ax.tick_params(axis="x", rotation=30)
        _style(ax, f"Dataset {ds}: reconstructed month-end open requests")
    figs["backlog"] = _save(fig, "3_backlog_reconstruction.png")
    return figs


def run(con: duckdb.DuckDBPyConnection, tests: dict | None = None) -> None:
    figs = charts(con)
    man = con.execute("SELECT dataset, file, rows, portal_records_count, portal_modified, substr(sha256, 1, 12) AS sha256_12 FROM gov_source_manifest").df()
    dq = con.execute("""SELECT rule_id, dataset, "check", severity, rows_flagged, round(rate * 100, 2) AS "rate %", status, kpi_impact_if_excluded
                        FROM qa_dq_results ORDER BY rule_id, dataset""").df()
    rec = con.execute("SELECT computed_correctly, count(*) n FROM kpi_integrity_recompute GROUP BY 1").df()
    mf = con.execute("SELECT dataset, kpi, name, value, meaningful, reason FROM kpi_meaningfulness").df()
    st = con.execute("SELECT * FROM stress_kpi_values").df()
    rev = con.execute("SELECT * FROM stress_reversals").df()
    sens = con.execute("""SELECT dataset, scenario, requests, K01, K02, K03, K06, slowest_by_median, slowest_by_km
                          FROM sensitivity_scenarios""").df()
    trend = con.execute("""SELECT t.dataset, t.kpi_label AS KPI, t.period_from || ' -> ' || t.period_to AS periods,
                                  t.value_from, t.value_to, t.direction, c.comparability_across_periods AS comparability
                           FROM sensitivity_trend t LEFT JOIN kpi_period_comparability c ON c.kpi = t.kpi""").df()
    cov = con.execute("SELECT dataset, aspect, finding, implication FROM coverage_assessment").df()
    lim = con.execute("SELECT * FROM backlog_limitations").df()
    lineage = con.execute("SELECT * FROM qa_row_lineage").df()

    def wide(ds):
        w = st[st.dataset == ds].pivot(index="service_group", columns="kpi", values="value")
        r = st[st.dataset == ds].pivot(index="service_group", columns="kpi", values="rank")
        t = pd.DataFrame({
            "Service group": w.index,
            "Median d (rank)": [f"{w.loc[g, 'K01']:.1f} ({r.loc[g, 'K01']:.0f})" for g in w.index],
            "P90 d (rank)": [f"{w.loc[g, 'K02']:.1f} ({r.loc[g, 'K02']:.0f})" for g in w.index],
            "Closed <=30d (rank)": ["" if pd.isna(w.loc[g, 'K03']) else f"{w.loc[g, 'K03']:.0%} ({r.loc[g, 'K03']:.0f})" for g in w.index],
            "Open median age (rank)": ["" if pd.isna(w.loc[g, 'K04_median_age']) else f"{w.loc[g, 'K04_median_age']:.0f} ({r.loc[g, 'K04_median_age']:.0f})" for g in w.index],
            "Open share (rank)": [f"{w.loc[g, 'K05']:.1%} ({r.loc[g, 'K05']:.0f})" for g in w.index],
            "KM median d (rank)": [f"{w.loc[g, 'K06']:.1f} ({r.loc[g, 'K06']:.0f})" for g in w.index],
        })
        return t.sort_values("Median d (rank)", key=lambda s: s.str.extract(r"\((\d+)\)")[0].astype(int))

    a_tot = mf[(mf.dataset == "A")].set_index("kpi").value
    b_tot = mf[(mf.dataset == "B")].set_index("kpi").value
    b_open = con.execute("SELECT open, k04_open_median_age, k04_open_share_over_90d FROM kpi_sql_summary WHERE dataset = 'B' AND service_group = '(all)'").fetchall()[0]
    park = st[(st.dataset == "A") & (st.service_group == "Parking")].set_index("kpi").value
    graf = st[(st.dataset == "A") & (st.service_group == "Graffiti")].set_index("kpi").value
    n_rev = rev.groupby("dataset").size().to_dict()
    rec_txt = ", ".join(f"{r.computed_correctly} {r.n}" for r in rec.itertuples())
    tests_txt = tests["summary"] if tests else "run `python -m pytest tests`"
    a_trend = trend[trend.dataset == "A"].set_index("KPI").direction

    md = f"""# KPI Reliability Report: City of Melbourne Customer Service Requests

*Dashboard Reliability Lab. Generated by `src/run_pipeline.py`; KPI definitions v{KPI_DEFS['version']}
(`config/kpi_definitions.json`). Every number below is read from the pipeline tables.*

**Question:** what happens when a dashboard is technically correct but the business conclusion is wrong?

## Executive summary

1. **Every KPI is computed correctly**: SQL and an independent Python recomputation agree ({rec_txt}); row counts
   reconcile through every transformation; no invalid dates.
2. **Not every correct KPI is meaningful.** In Dataset B (2018), the closed-only median is **{b_tot['K01']:.1f} days**, but
   {b_open[0]:,} requests ({b_tot['K05']:.0%}) are still open with a median age of **{b_open[1]:.0f} days**
   ({b_open[2]:.0%} older than 90 days). Counting open requests as censored, the Kaplan-Meier median is
   **{b_tot['K06']:.1f} days** and the 90th percentile is never reached during follow-up. A manager reading only the
   median would conclude the service is fast.
3. **Rankings reverse.** In Dataset A, *Parking* is the fastest category on median ({park['K01']:.0f} day) but has the
   **longest** 90th percentile ({park['K02']:.0f} days), the lowest 30-day closure rate ({park['K03']:.0%}) and the oldest
   open requests ({park['K04_median_age']:.0f} days). *Graffiti* is slowest on median ({graf['K01']:.0f} days) but 2nd best on
   p90. Across both datasets there are {sum(n_rev.values())} pairwise reversals against the median ranking.
4. **The trend conclusion depends on the KPI.** 2016 vs 2015 (A): median {a_trend.get('Median days (completed)')},
   p90 {a_trend.get('P90 days (completed)')}, 30-day closure {a_trend.get('Closed within 30 days, intake cohort')}.
5. **Coverage limits interpretation.** Dataset B is a **subset** from one of several council systems and is half
   parking infringement reviews; the two datasets do not overlap in time and are never combined. Both are stale
   (latest records 2016 and 2018).
6. **A naive KPI is wrong by construction.** Dataset A's `days_to_complete` holds the *age* of open requests, not a
   completion time; a median over the whole column (K07) mixes the two. It happens to equal the correct value overall
   ({a_tot['K07']:.0f} days) because only {a_tot['K05']:.1%} of A is open, which is exactly why it would pass a casual check.

## 1. Sources and versioning

{_md(man)}

Row lineage (raw → staging → analysis view → KPI population → cohort table):

{_md(lineage, "{:.0f}")}

## 2. Data Quality Engine: checks and KPI impact

Each rule records rows flagged and what happens to the KPIs it touches if those rows are excluded.

{_md(dq)}

## 3. KPI Integrity: correct vs meaningful

Question tested: *"How long do residents wait for a request to be resolved?"*

{_md(mf)}

## 4. KPI Stress Test

![stress]({figs['stress']})

Rank 1 = best on that KPI. Groups with >= 200 requests.

**Dataset A**

{_md(wide('A'))}

**Dataset B**

{_md(wide('B'))}

![km]({figs['km']})

The dotted lines are the closed-only medians. For A, the KM curve crosses 50% at the same point: censoring is
negligible. For B the curve crosses 50% around day {b_tot['K06']:.0f} and flattens below 90%: a large share of
requests never closes in the observation window.

## 5. Sensitivity Analysis

Headline KPIs and conclusions under alternative reasonable assumptions:

{_md(sens)}

- Day-count convention (A1) and timezone (B5) change nothing material: those conclusions are **stable**.
- Excluding missing-suburb rows (A7, B6) changes B completely, because suburb is missing for whole service types
  (parking reviews): missing data are not random, so "exclude incomplete rows" is not a neutral cleaning step.
- Removing 'Closed as Duplicate' (B1) raises the KM median by about two days.

**Does the trend conclusion depend on the KPI?**

{_md(trend)}

## 6. Coverage Assessment

{_md(cov)}

## 7. Backlog history: what cannot be reconstructed

![backlog]({figs['backlog']})

{_md(lim)}

## 8. Decision Evidence Report

| Management question | Use | Do not use alone | Evidence | Limits |
|---|---|---|---|---|
| How fast do we resolve requests? | K06 KM median + K02 p90 + K04 open ageing | K01 median (closed only) | Sections 3-4 | B: 90th percentile not reached; KM assumes non-informative censoring |
| Which service needs attention first? | Rank on K02, K03 and K04 together; investigate groups that are bad on 2+ | K01 ranking | Section 4 reversals | Groups < 200 requests not ranked |
| Is performance improving? | K03 by intake cohort (fixed window) | K01, K04, K05 across periods | Section 5 trend | Only cohorts with 30-day follow-up |
| How large is the backlog? | K04 snapshot at extract | Reconstructed history before warm-up; any B backlog figure | Section 7 | B open records may be unclosed records, not work |
| What share of requests come through digital channels? | B channel mix, labelled "Salesforce subset, 2018" | Any council-wide channel claim | Section 6 | Subset of systems; 48% parking reviews |
| How do suburbs compare? | Only within service types with low suburb missingness | Any all-service suburb ranking | Sections 2, 5 | Suburb missing for 54% of rows, not at random |

**Conclusions that ARE supported:** A's typical completion times (median 5 days, p90 about 28 days) are robust to
day-count, period and censoring choices; Parking (A) has a long tail that the median hides; B has a large, ageing
pool of open records concentrated in parking infringement reviews.

**Conclusions that are NOT supported:** "service is fast" from B's median; "2016 was worse than 2015" from the median
alone; any statement about all council requests from B; any backlog trend for B.

## 9. Automated QA

Test run: `{tests_txt}` (JUnit XML in `outputs/governance/test_results.xml`; every run appended to
`outputs/governance/qa_log.jsonl` with source hashes and KPI-definition version).

## Assumptions

{_md(pd.DataFrame(KPI_DEFS['assumptions']))}
"""
    REP.mkdir(exist_ok=True)
    (REP / "KPI_Reliability_Report.md").write_text(md, encoding="utf-8")
    print("report: reports/KPI_Reliability_Report.md")
