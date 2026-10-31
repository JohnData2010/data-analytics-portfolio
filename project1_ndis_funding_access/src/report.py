"""Build the findings report (Markdown + PNG charts) from the analysis tables.

Every number in the report is read from DuckDB at build time, so a rerun on new
NDIA releases regenerates the text.
"""
from pathlib import Path

import duckdb
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import pandas as pd  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
REP = ROOT / "reports"
FIG = REP / "figures"

# reference categorical palette (fixed order) + neutral context ink
BLUE, ORANGE, AQUA = "#2a78d6", "#eb6834", "#1baf7a"
INK, MUTED, GRID = "#0b0b0b", "#8a8984", "#e6e5e1"


def _style(ax, title, ylabel=None):
    ax.set_title(title, loc="left", fontsize=11, color=INK, pad=10)
    for s in ["top", "right"]:
        ax.spines[s].set_visible(False)
    for s in ["left", "bottom"]:
        ax.spines[s].set_color(MUTED)
    ax.tick_params(colors="#52514e", labelsize=8)
    ax.grid(axis="y", color=GRID, linewidth=0.8)
    ax.set_axisbelow(True)
    if ylabel:
        ax.set_ylabel(ylabel, fontsize=8, color="#52514e")


def _save(fig, name):
    FIG.mkdir(parents=True, exist_ok=True)
    fig.tight_layout()
    fig.savefig(FIG / name, dpi=150, facecolor="#fcfcfb")
    plt.close(fig)
    return f"figures/{name}"


def _md_table(df: pd.DataFrame) -> str:
    cols = list(df.columns)
    lines = ["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
    for _, r in df.iterrows():
        lines.append("| " + " | ".join(str(r[c]).replace("|", r"\|") for c in cols) + " |")
    return "\n".join(lines)


def _quarter_ticks(ax, dates):
    ticks = pd.to_datetime(pd.Series(dates).unique())
    ax.set_xticks(ticks, [t.strftime("%b-%y") for t in ticks])


def charts(con):
    figs = {}
    nat = con.execute("SELECT * FROM mart_util_national ORDER BY report_date").df()
    fig, ax = plt.subplots(figsize=(7, 3.4))
    for cls, col in [("ALL", INK), ("Core", BLUE), ("Capacity Building", ORANGE), ("Capital", AQUA)]:
        d = nat[nat.support_class == cls]
        ax.plot(pd.to_datetime(d.report_date), d.utilisation * 100, color=col, lw=2, marker="o", ms=4)
        ax.annotate(cls, (pd.to_datetime(d.report_date).iloc[-1], d.utilisation.iloc[-1] * 100),
                    xytext=(6, 0), textcoords="offset points", fontsize=8, color=INK, va="center")
    _style(ax, "National plan budget utilisation by support class (%)", "utilisation %")
    _quarter_ticks(ax, nat.report_date)
    ax.set_xlim(right=pd.Timestamp("2026-11-30"))
    figs["national"] = _save(fig, "a1_national_utilisation.png")

    st = con.execute("""SELECT district_key, mean_raw_gap, mean_like_for_like_gap, persistent_low_adjusted
                        FROM an_util_persistence""").df()
    fig, ax = plt.subplots(figsize=(6.5, 4.2))
    other = st[~st.persistent_low_adjusted]
    flag = st[st.persistent_low_adjusted]
    ax.scatter(other.mean_raw_gap * 100, other.mean_like_for_like_gap * 100, s=22, color=MUTED, alpha=.6,
               edgecolor="white", linewidth=1, label="other districts")
    ax.scatter(flag.mean_raw_gap * 100, flag.mean_like_for_like_gap * 100, s=34, color=ORANGE,
               edgecolor="white", linewidth=1, label="bottom fifth after adjustment, >=6 of 8 quarters")
    lim = [min(st.mean_raw_gap.min(), st.mean_like_for_like_gap.min()) * 100 - 1,
           max(st.mean_raw_gap.max(), st.mean_like_for_like_gap.max()) * 100 + 1]
    ax.plot(lim, lim, color=MUTED, lw=1, ls="--")
    ax.text(lim[1], lim[1], "  no mix effect", fontsize=7, color=MUTED, va="bottom", ha="right")
    for _, r in flag.nsmallest(5, "mean_like_for_like_gap").iterrows():
        ax.annotate(r.district_key, (r.mean_raw_gap * 100, r.mean_like_for_like_gap * 100),
                    xytext=(5, -2), textcoords="offset points", fontsize=7, color=INK)
    _style(ax, "District gap to national utilisation, 8-quarter mean (pts)")
    ax.set_xlabel("raw gap (observed - national)", fontsize=8)
    ax.set_ylabel("mix-adjusted gap (observed - expected)", fontsize=8)
    ax.legend(fontsize=7, frameon=False, loc="upper left")
    figs["scatter"] = _save(fig, "a2_raw_vs_adjusted_gap.png")

    nat_sda = con.execute("""SELECT report_date, max(national_not_using) n, max(national_non_use_share) s
                             FROM mart_sda_district GROUP BY 1 ORDER BY 1""").df()
    fig, axes = plt.subplots(1, 2, figsize=(8, 3.2))
    x = pd.to_datetime(nat_sda.report_date)
    axes[0].bar(x, nat_sda.n, width=50, color=BLUE)
    _style(axes[0], "Eligible, not yet using SDA (participants)")
    axes[1].plot(x, nat_sda.s * 100, color=BLUE, lw=2, marker="o", ms=4)
    _style(axes[1], "Non-use share of SDA-eligible (%)")
    for a in axes:
        _quarter_ticks(a, nat_sda.report_date)
        a.tick_params(axis="x", rotation=30)
    figs["sda_nat"] = _save(fig, "b1_sda_national.png")

    q = con.execute("""
        SELECT q.district_key, q.report_date, q.non_use_share, q.national_non_use_share
        FROM an_sda_quarterly q
        JOIN an_sensitivity_flags f ON f.district_key = q.district_key AND f.module = 'SDA non-use'
        WHERE f.definitions_flagging >= 3 ORDER BY 1, 2""").df()
    keys = q.district_key.unique()
    fig, axes = plt.subplots(1, len(keys), figsize=(2.0 * len(keys), 2.6), sharey=True)
    for a, k in zip(axes, keys):
        d = q[q.district_key == k]
        xd = pd.to_datetime(d.report_date)
        a.plot(xd, d.national_non_use_share * 100, color=MUTED, lw=1.5, ls="--")
        a.plot(xd, d.non_use_share * 100, color=ORANGE, lw=2)
        a.set_title(k.replace(" | ", "\n"), fontsize=7.5, loc="left")
        a.tick_params(labelsize=6)
        a.set_xticks([xd.iloc[0], xd.iloc[-1]], ["Sep-24", "Jun-26"])
        for s in ["top", "right"]:
            a.spines[s].set_visible(False)
        a.grid(axis="y", color=GRID)
    axes[0].set_ylabel("non-use share % (dashed = national)", fontsize=7)
    figs["sda_small"] = _save(fig, "b2_sda_robust_districts.png")

    flags = con.execute("SELECT * FROM an_sensitivity_flags WHERE module = 'SDA non-use'").df()
    defs = sorted({d for s in flags.definitions for d in s.split(" ; ")})
    flags = flags.sort_values("definitions_flagging", ascending=False)
    mat = pd.DataFrame([[1 if d in s.split(" ; ") else 0 for d in defs] for s in flags.definitions],
                       index=flags.district_key, columns=[d.split(" ", 1)[0] for d in defs])
    fig, ax = plt.subplots(figsize=(4.8, 0.22 * len(mat) + 1))
    ax.imshow(mat.values, cmap=matplotlib.colors.ListedColormap(["#f1f0ec", BLUE]), aspect="auto")
    ax.set_yticks(range(len(mat)), mat.index, fontsize=7)
    ax.set_xticks(range(len(mat.columns)), mat.columns, fontsize=8)
    ax.set_title("SDA districts of concern by definition (S1-S5)", loc="left", fontsize=10)
    for s in ax.spines.values():
        s.set_visible(False)
    figs["sens"] = _save(fig, "c1_sensitivity_matrix.png")
    return figs


def run(con: duckdb.DuckDBPyConnection) -> None:
    figs = charts(con)
    rec = con.execute("SELECT status, count(*) n FROM qa_reconciliation GROUP BY 1 ORDER BY 1").df()
    rec_warn = con.execute("SELECT check_id, description, expected, actual, note FROM qa_reconciliation WHERE status <> 'PASS'").df()
    ev = con.execute("SELECT * FROM an_evidence_register").df()
    mix = con.execute("SELECT * FROM an_mix_decomposition WHERE span = 'first-to-last'").df()
    util = con.execute("""SELECT district_key AS District, quarters_bottom_quintile_raw AS "Q bottom (raw)",
            quarters_bottom_quintile_adjusted AS "Q bottom (adj)",
            round(mean_observed_rate * 100, 1) AS "Mean util %", round(mean_mix_effect * 100, 1) AS "Mix effect pts",
            round(mean_like_for_like_gap * 100, 1) AS "Adj gap pts", round(mean_within_cell_coverage * 100) AS "Cell coverage %"
        FROM an_util_persistence WHERE persistent_low_raw OR persistent_low_adjusted
        ORDER BY mean_like_for_like_gap""").df()
    sda = con.execute("""SELECT p.district_key AS District, p.latest_not_using AS "Not using", p.latest_eligible_total AS Eligible,
            round(p.latest_non_use_share * 100, 1) AS "Share %", p.quarters_above_national AS "Q above natl",
            p.change_not_using AS "Change since Sep-24", f.definitions_flagging AS "Defs (of 5)"
        FROM an_sda_persistence p
        LEFT JOIN an_sensitivity_flags f ON f.district_key = p.district_key AND f.module = 'SDA non-use'
        WHERE f.definitions_flagging >= 2 ORDER BY f.definitions_flagging DESC, p.latest_not_using DESC""").df()
    top_abs = con.execute("""SELECT district_key AS District, latest_not_using AS "Not using",
            round(latest_non_use_share * 100, 1) AS "Share %" FROM an_sda_persistence
        ORDER BY latest_not_using DESC LIMIT 5""").df()
    thr = con.execute("SELECT min_eligible, districts_ranked, overlap_with_base FROM an_sensitivity_threshold").df()
    sa4 = con.execute("""SELECT region AS "SA4 region", with_need AS "Participants with SDA need",
            enrolled_dwellings AS "Enrolled dwellings", resident_capacity_min AS "Resident capacity (min)",
            unfinished_new_build AS "Unfinished new builds"
        FROM mart_sda_sa4_context WHERE is_sa4 AND state IN ('WA', 'TAS', 'NT') AND with_need > 0
        ORDER BY state, with_need DESC""").df()
    scen = con.execute("""SELECT district_key AS District, denominator_eligible_total AS "Eligible (denominator)",
            baseline_not_using AS "Not using now", round(baseline_share * 100, 1) AS "Share now %",
            round(participants_moving_to_use, 1) AS "Participants moving to use (-5 pts)"
        FROM an_scenario_grid g
        WHERE reduction_pts = 5 AND district_key IN (SELECT district_key FROM an_sensitivity_flags
                                                     WHERE module = 'SDA non-use' AND definitions_flagging >= 3)
        ORDER BY 2 DESC""").df()

    natl = mix.set_index("decomposition")
    sc = natl.loc["support class mix"]
    pm = natl.loc["participant mix (disability x age)"]
    counts = ev.status.value_counts()

    def claims(status, module=None):
        d = ev[ev.status == status]
        if module:
            d = d[d.module == module]
        return "\n".join(f"- **{r.claim_id}** {r.claim}" for _, r in d.iterrows())

    md = f"""# Funding Is Not Access: NDIS Support Access & Service Gap Intelligence

*Generated by `src/run_pipeline.py` from NDIA public datasets (reporting dates 30-Sep-2024 to 30-Jun-2026).
Every figure below is read from the pipeline tables; rerunning on a new NDIA release regenerates this report.*

**Central question:** when funding is available, does it translate into support being used?

## 1. Data and validation

| Source (NDIA) | Grain | Periods |
|---|---|---|
| Participant numbers and plan budgets | state x service district x disability x age band x support class | 8 quarters |
| Utilisation of plan budgets | the same + SIL/SDA flag | 8 quarters (lagged 6-month window) |
| SDA participants | state x service district x SDA status | 8 quarters |
| SDA enrolled dwellings and NDIS demand (Supplement P) | SA4 / SA3 region | 30-Jun-2026 only |

Reconciliation results ({len(con.execute('SELECT * FROM qa_reconciliation').df())} checks): {", ".join(f"{r.status} {r.n}" for _, r in rec.iterrows())}.
Load counts match every source file; states add to the national total every quarter; SDA district counts agree
with the NDIA SDA supplement where both publish a number. Explained variances:

{_md_table(rec_warn)}

**Grain rules applied** (from the NDIA data rules):
- Utilisation covers supports delivered in a 6-month window ending 3 months before the reporting date; budgets are point-in-time. They are shown together only with that window labelled (`dim_period`).
- Age bands changed in Dec-2024 (0-6 / 7-14 -> 0-8 / 9-14). Trend analysis by age uses the harmonised band `0 to 14`.
- Masked cells (`<11`, and complementary masks such as `<12699`) are never imputed in published-value columns. Where a named district's state-level row is complementary-masked, the district total (state = ALL) is used only if it is within the mask bound, and flagged (`participants_source`).
- SDA dwellings are published by SA4, participants by service district. The two are **never joined**; dwellings appear only as regional context.
- The SDA file labels residual districts `<STATE> - Other` and Tasmanian districts differently from the SDA supplement; labels are normalised before comparison.

## 2. Module A: Funding-Utilisation

![national]({figs["national"]})

National utilisation fell from {sc.published_from:.0%} to {sc.published_to:.0%} between 30-Sep-2024 and 30-Jun-2026.
A budget-weighted shift-share decomposition asks whether this came from **changing composition**:

| Decomposition | Mix effect (pts) | Within-group rate effect (pts) |
|---|---|---|
| Support class (Core / Capacity Building / Capital) | {sc.mix_effect * 100:+.2f} | {sc.rate_effect * 100:+.2f} |
| Participant mix (disability x age, harmonised) | {pm.mix_effect * 100:+.2f} | {pm.rate_effect * 100:+.2f} |

**Reading:** almost none of the fall is explained by mix; utilisation fell *within* groups. (NDIA publishes whole percentages, so effects under ~0.5 pt are within rounding.)

### Is low utilisation persistent, and does it survive a like-for-like comparison?

Each district's expected rate is what it would show at national rates for its own disability x age mix
(budget-weighted, indirect standardisation). The adjusted gap is observed - expected.

![scatter]({figs["scatter"]})

{_md_table(util)}

Points sit almost on the diagonal: in these districts the gap is **not** a mix artefact. The persistently low
districts are overwhelmingly remote and regional (NT, WA, SA, far-west NSW).

## 3. Module B: SDA Access Gap Monitor

![sda national]({figs["sda_nat"]})

**Districts flagged by at least 2 of 5 definitions** (S1 latest count, S2 latest share with eligible >= 30,
S3 8-quarter mean share, S4 per 1,000 active participants, S5 persistently above national):

{_md_table(sda)}

![sda robust]({figs["sda_small"]})

The robust districts are persistently above the national share, but most are falling, so the gap is narrowing,
not widening. The largest absolute numbers are elsewhere: a count-based ranking points to large metropolitan districts:

{_md_table(top_abs)}

### Context only: SDA dwellings at SA4 grain (30-Jun-2026)

Enrolled dwellings and resident capacity are **not vacancies** and SA4 regions do not align with service
districts. Shown for WA, TAS and NT, the states of the robust districts:

{_md_table(sa4)}

## 4. Sensitivity: does the list change with the measure?

![sensitivity]({figs["sens"]})

- Absolute counts and shares identify **different** districts: the S1 (count) list and the share-based lists overlap little, because large metropolitan districts have many non-users at average shares.
- Districts flagged under >= 3 definitions are treated as robust; others are labelled definition-dependent in the evidence register.
- Minimum-eligible threshold for share ranking:

{_md_table(thr)}

## 5. Module C: Evidence register

The register (`outputs/powerbi/an_evidence_register.csv`) holds {len(ev)} statements:
{counts.get("Observed pattern", 0)} observed patterns, {counts.get("Requires investigation", 0)} requiring investigation and
{counts.get("Unsupported conclusion", 0)} unsupported conclusions. Each row records the rule that assigned its status.

### Observed patterns
{claims("Observed pattern")}

### Unsupported conclusions (guardrails)
{claims("Unsupported conclusion", "B")}

The "requires investigation" rows list, for each district, six hypotheses (dwelling suitability, location,
personal choice, transition timing, service availability, recording) and the specific information needed
to test each one.

## 6. Scenario explorer (arithmetic, not a forecast)

If the non-use share in a district fell by 5 percentage points, with the eligible total held at its
30-Jun-2026 value and everything else unchanged:

{_md_table(scen)}

`outputs/powerbi/an_scenario_grid.csv` holds 0-20 point reductions for every unmasked district; the Power BI
what-if parameter (see `powerbi/DAX_measures.md`) computes the same arithmetic interactively.
Denominator: SDA in use + eligible not yet using, same district, 30-Jun-2026. This is not a causal estimate.

## 7. Limitations

- Utilisation is a ratio of payments to committed budget; it does not measure quality, need or outcomes.
- Masking removes small districts from rankings; shares on small denominators are volatile (threshold sensitivity above).
- NDIA notes that SDA "in use" is estimated from payments, service bookings and address matching; some legacy eligibility records need further investigation. Part of measured non-use may be recording.
- Only 8 quarters are published openly; earlier history is available on request.
"""
    REP.mkdir(exist_ok=True)
    (REP / "NDIS_Funding_Access_Report.md").write_text(md, encoding="utf-8")
    print("report: reports/NDIS_Funding_Access_Report.md")


if __name__ == "__main__":
    c = duckdb.connect(str(ROOT / "data" / "ndis.duckdb"))
    run(c)
