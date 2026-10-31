"""Module A (funding-utilisation), Module B (SDA access gap), sensitivity and scenario tables.

All outputs are DuckDB tables prefixed `an_`; export.py writes them for Power BI.
"""
import duckdb
import numpy as np
import pandas as pd

LATEST = pd.Timestamp("2026-06-30")
MIN_ELIGIBLE = 30          # SDA shares on fewer eligible participants are too volatile to rank
TOP_N = 10
PERSIST_MIN_QUARTERS = 6   # "persistent" = flagged in at least 6 of 8 quarters


def _save(con, name, df):
    con.register("tmp_df", df)
    con.execute(f"CREATE OR REPLACE TABLE {name} AS SELECT * FROM tmp_df")
    con.unregister("tmp_df")


# --------------------------------------------------------------------------- Module A
def util_persistence(con) -> pd.DataFrame:
    """Is low utilisation consistent across quarters, before and after mix adjustment?"""
    s = con.execute("""
        SELECT * FROM mart_util_standardised
        WHERE state NOT IN ('State_Missing', 'OT') AND district <> 'Other'
    """).df()
    s["report_date"] = pd.to_datetime(s.report_date)
    for col, flag in [("observed_rate", "bottom_q_raw"), ("like_for_like_gap", "bottom_q_adj")]:
        s[flag] = s.groupby("report_date")[col].transform(lambda x: x <= x.quantile(0.2))
    first, last = s.report_date.min(), s.report_date.max()

    def slope(g):
        g = g.dropna(subset=["observed_rate"])
        if len(g) < 3:
            return np.nan
        x = (g.report_date - first).dt.days / 91.3
        return np.polyfit(x, g.observed_rate, 1)[0]

    out = s.groupby(["district_key", "state", "district"]).agg(
        quarters_observed=("observed_rate", "count"),
        quarters_bottom_quintile_raw=("bottom_q_raw", "sum"),
        quarters_bottom_quintile_adjusted=("bottom_q_adj", "sum"),
        mean_observed_rate=("observed_rate", "mean"),
        mean_raw_gap=("raw_gap", "mean"),
        mean_mix_effect=("mix_effect", "mean"),
        mean_like_for_like_gap=("like_for_like_gap", "mean"),
        mean_within_cell_gap=("within_cell_gap", "mean"),
        mean_within_cell_coverage=("within_cell_coverage", "mean"),
    ).reset_index()
    out["trend_pts_per_quarter"] = out.district_key.map(
        s.groupby("district_key").apply(slope, include_groups=False)) * 100
    latest = s[s.report_date == last].set_index("district_key")
    out["latest_observed_rate"] = out.district_key.map(latest.observed_rate)
    out["latest_like_for_like_gap"] = out.district_key.map(latest.like_for_like_gap)
    out["latest_active_participants"] = out.district_key.map(latest.active_participants)
    out["persistent_low_raw"] = out.quarters_bottom_quintile_raw >= PERSIST_MIN_QUARTERS
    out["persistent_low_adjusted"] = out.quarters_bottom_quintile_adjusted >= PERSIST_MIN_QUARTERS
    # "explained by mix" needs a material mix effect (>= 1 pt), not just a quintile boundary shift
    out["low_mostly_mix"] = out.persistent_low_raw & ~out.persistent_low_adjusted & (out.mean_mix_effect <= -0.01)
    _save(con, "an_util_persistence", out)
    _save(con, "an_util_quarterly_flags", s)
    return out


def _shift_share(df, cell_cols, label):
    """Symmetric shift-share of the national rate between consecutive quarters.

    rate_t ~= sum_c w_ct r_ct / sum_c w_ct.  delta = mix (weights move) + rate (cell rates move).
    """
    df = df.dropna(subset=["rate", "weight"]).copy()
    df["share"] = df.weight / df.groupby("report_date").weight.transform("sum")
    dates = sorted(df.report_date.unique())
    rows = []
    for d0, d1 in list(zip(dates[:-1], dates[1:])) + [(dates[0], dates[-1])]:
        a = df[df.report_date == d0].set_index(cell_cols)
        b = df[df.report_date == d1].set_index(cell_cols)
        idx = a.index.intersection(b.index)
        a, b = a.loc[idx].copy(), b.loc[idx].copy()
        # re-normalise on the common cells so mix + rate adds up exactly
        a["share"], b["share"] = a.share / a.share.sum(), b.share / b.share.sum()
        mix = ((b.share - a.share) * (a.rate + b.rate) / 2).sum()
        rate = ((b.rate - a.rate) * (a.share + b.share) / 2).sum()
        rows.append(dict(decomposition=label, from_date=d0, to_date=d1,
                         rate_from=(a.share * a.rate).sum() / a.share.sum(),
                         rate_to=(b.share * b.rate).sum() / b.share.sum(),
                         mix_effect=mix, rate_effect=rate, cells=len(idx),
                         span="first-to-last" if (d0, d1) == (dates[0], dates[-1]) else "quarter-on-quarter"))
    return pd.DataFrame(rows)


def mix_decomposition(con) -> pd.DataFrame:
    """Did a change in the mix of supports / participants move the national average?"""
    sc = con.execute("""
        SELECT u.report_date, u.support_class, u.utilisation AS rate,
               b.participants * b.avg_annualised_budget AS weight
        FROM stg_utilisation u
        JOIN stg_plan_budgets b
          ON b.report_date = u.report_date AND b.state = 'ALL' AND b.district = 'ALL'
         AND b.disability_group = 'ALL' AND b.age_band = 'ALL' AND b.support_class = u.support_class
        WHERE u.state = 'ALL' AND u.district = 'ALL' AND u.disability_group = 'ALL'
          AND u.age_band = 'ALL' AND u.sil_or_sda = 'ALL' AND u.support_class <> 'ALL'
    """).df()
    # participant mix: disability x harmonised age band ('0 to 14' comparable across all 8 quarters)
    pm = con.execute("""
        SELECT u.report_date, u.disability_group, u.age_band_h,
               sum(u.utilisation * b.participants * b.avg_annualised_budget)
                 / sum(b.participants * b.avg_annualised_budget) AS rate,
               sum(b.participants * b.avg_annualised_budget)     AS weight
        FROM stg_utilisation u
        JOIN stg_plan_budgets b
          ON b.report_date = u.report_date AND b.state = 'ALL' AND b.district = 'ALL'
         AND b.disability_group = u.disability_group AND b.age_band = u.age_band AND b.support_class = 'ALL'
        WHERE u.state = 'ALL' AND u.district = 'ALL' AND u.sil_or_sda = 'ALL' AND u.support_class = 'ALL'
          AND u.disability_group <> 'ALL' AND u.age_band <> 'ALL'
        GROUP BY ALL
    """).df()
    out = pd.concat([_shift_share(sc, ["support_class"], "support class mix"),
                     _shift_share(pm, ["disability_group", "age_band_h"], "participant mix (disability x age)")])
    pub = con.execute("SELECT report_date, utilisation FROM mart_util_national WHERE support_class = 'ALL'").df()
    pub = dict(zip(pd.to_datetime(pub.report_date), pub.utilisation))
    out["published_from"] = pd.to_datetime(out.from_date).map(pub)
    out["published_to"] = pd.to_datetime(out.to_date).map(pub)
    out["reconstruction_error_to"] = out.rate_to - out.published_to
    _save(con, "an_mix_decomposition", out)
    return out


# --------------------------------------------------------------------------- Module B
def sda_persistence(con) -> pd.DataFrame:
    s = con.execute("SELECT * FROM mart_sda_district WHERE state <> 'MIS'").df()
    s["report_date"] = pd.to_datetime(s.report_date)
    s["above_national"] = s.non_use_share > s.national_non_use_share
    s["excess_pts"] = (s.non_use_share - s.national_non_use_share) * 100
    first = s.report_date.min()
    g = s.groupby(["district_key", "state", "district"])
    out = g.agg(
        quarters_published=("non_use_share", "count"),
        quarters_above_national=("above_national", "sum"),
        mean_non_use_share=("non_use_share", "mean"),
        mean_excess_pts=("excess_pts", "mean"),
        mean_not_using=("not_using", "mean"),
    ).reset_index()
    last = s[s.report_date == LATEST].set_index("district_key")
    frst = s[s.report_date == first].set_index("district_key")
    for c in ["in_use", "not_using", "eligible_total", "non_use_share", "non_use_share_min",
              "non_use_share_max", "any_masked", "not_using_per_1000_active", "active_participants"]:
        out[f"latest_{c}"] = out.district_key.map(last[c])
    out["first_not_using"] = out.district_key.map(frst.not_using)
    out["change_not_using"] = out.latest_not_using - out.first_not_using
    out["change_share_pts"] = (out.latest_non_use_share - out.district_key.map(frst.non_use_share)) * 100
    out["persistent_above_national"] = (out.quarters_above_national >= PERSIST_MIN_QUARTERS)
    _save(con, "an_sda_persistence", out)
    _save(con, "an_sda_quarterly", s)
    return out


# --------------------------------------------------------------------------- sensitivity
def _top(df, col, n=TOP_N, ascending=False):
    return set(df.dropna(subset=[col]).sort_values(col, ascending=ascending).head(n).district_key)


def sensitivity(con, sda: pd.DataFrame, util: pd.DataFrame) -> pd.DataFrame:
    """Do the 'districts of concern' change when the definition changes?"""
    big = sda[sda.latest_eligible_total >= MIN_ELIGIBLE]
    multi = sda[(sda.quarters_published >= PERSIST_MIN_QUARTERS)]
    sda_defs = {
        "S1 latest count not using": _top(sda, "latest_not_using"),
        "S2 latest non-use share (eligible>=30)": _top(big, "latest_non_use_share"),
        "S3 8-quarter mean share (>=6 qtrs)": _top(multi, "mean_non_use_share"),
        "S4 latest not-using per 1,000 active": _top(sda, "latest_not_using_per_1000_active"),
        "S5 above national >=6 qtrs, top mean excess": _top(
            sda[sda.persistent_above_national], "mean_excess_pts"),
    }
    util_defs = {
        "U1 latest raw rate (lowest)": _top(util, "latest_observed_rate", ascending=True),
        "U2 latest mix-adjusted gap (lowest)": _top(util, "latest_like_for_like_gap", ascending=True),
        "U3 8-quarter mean raw rate (lowest)": _top(util, "mean_observed_rate", ascending=True),
        "U4 8-quarter mean adjusted gap (lowest)": _top(util, "mean_like_for_like_gap", ascending=True),
        "U5 bottom quintile adjusted >=6 qtrs": set(util[util.persistent_low_adjusted].district_key),
    }
    rows, overlap = [], []
    for module, defs in [("SDA non-use", sda_defs), ("Utilisation", util_defs)]:
        names = list(defs)
        all_keys = set().union(*defs.values())
        for k in sorted(all_keys):
            hits = [n for n in names if k in defs[n]]
            rows.append(dict(module=module, district_key=k, definitions_flagging=len(hits),
                             definitions=" ; ".join(hits),
                             robustness="robust (>=3 of 5)" if len(hits) >= 3
                             else "definition-dependent" if len(hits) == 2 else "single definition"))
        for a in names:
            for b in names:
                inter = len(defs[a] & defs[b])
                union = len(defs[a] | defs[b]) or 1
                overlap.append(dict(module=module, definition_a=a, definition_b=b,
                                    shared=inter, jaccard=inter / union))
    flags = pd.DataFrame(rows)
    _save(con, "an_sensitivity_flags", flags)
    _save(con, "an_sensitivity_overlap", pd.DataFrame(overlap))

    # threshold sensitivity: how many districts rank top-10 by share as MIN_ELIGIBLE moves
    th = []
    base = _top(big, "latest_non_use_share")
    for t in [0, 10, 20, 30, 50, 100]:
        cand = _top(sda[sda.latest_eligible_total >= t], "latest_non_use_share")
        th.append(dict(min_eligible=t, districts_ranked=len(sda[sda.latest_eligible_total >= t]),
                       overlap_with_base=len(cand & base), top10=" ; ".join(sorted(cand))))
    _save(con, "an_sensitivity_threshold", pd.DataFrame(th))
    return flags


# --------------------------------------------------------------------------- scenario
def scenario_grid(con) -> pd.DataFrame:
    """Scenario arithmetic, not a forecast: hold each district's eligible total at its
    30-Jun-2026 value and ask how many participants a lower non-use share corresponds to."""
    s = con.execute(f"""
        SELECT district_key, state, district, report_date, in_use, not_using, eligible_total, non_use_share
        FROM mart_sda_district
        WHERE report_date = DATE '{LATEST.date()}' AND NOT any_masked AND state <> 'MIS'
    """).df()
    rows = []
    for _, r in s.iterrows():
        for pts in range(0, 21):
            new_share = max(r.non_use_share - pts / 100, 0)
            new_not_using = new_share * r.eligible_total
            rows.append(dict(district_key=r.district_key, state=r.state, district=r.district,
                             denominator_date=r.report_date, denominator_eligible_total=r.eligible_total,
                             baseline_not_using=r.not_using, baseline_share=r.non_use_share,
                             reduction_pts=pts, scenario_share=new_share,
                             scenario_not_using=new_not_using,
                             participants_moving_to_use=r.not_using - new_not_using,
                             assumption="eligible total fixed at 30-Jun-2026; all else unchanged; not a forecast"))
    out = pd.DataFrame(rows)
    _save(con, "an_scenario_grid", out)
    return out


def run(con: duckdb.DuckDBPyConnection) -> None:
    util = util_persistence(con)
    mix_decomposition(con)
    sda = sda_persistence(con)
    sensitivity(con, sda, util)
    scenario_grid(con)
    print("analysis: an_* tables built")


if __name__ == "__main__":
    from pathlib import Path
    c = duckdb.connect(str(Path(__file__).resolve().parents[1] / "data" / "ndis.duckdb"))
    run(c)
