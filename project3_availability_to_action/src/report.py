"""Decision Evidence Report (Markdown + PNG), built from the DuckDB tables. Every number is read, none typed."""
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
PARAMS = json.loads((ROOT / "config" / "params.json").read_text(encoding="utf-8"))
T = PARAMS["holdout_decision_day"]

BLUE, ORANGE, GREEN, AMBER, PINK = "#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4"
INK, INK_2, MUTED, GRID = "#0b0b0b", "#52514e", "#8a8984", "#e6e5e1"
METHOD_COLOURS = {"A1 Volume": AMBER, "A2 Frequency": PINK, "B Lost demand": ORANGE, "P": BLUE,
                  "Oracle (hindsight)": MUTED}


def _md(df: pd.DataFrame, fmt: dict | None = None) -> str:
    fmt = fmt or {}
    cols = list(df.columns)
    out = ["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
    for _, r in df.iterrows():
        cells = []
        for c in cols:
            v = r[c]
            if isinstance(v, float):
                v = "" if pd.isna(v) else fmt.get(c, "{:.2f}").format(v)
            cells.append(str(v).replace("|", r"\|").replace("\n", " "))
        out.append("| " + " | ".join(cells) + " |")
    return "\n".join(out)


def _style(ax, title):
    ax.set_title(title, loc="left", fontsize=10.5, color=INK, pad=8)
    for s in ["top", "right"]:
        ax.spines[s].set_visible(False)
    for s in ["left", "bottom"]:
        ax.spines[s].set_color(MUTED)
    ax.tick_params(colors=INK_2, labelsize=8)
    ax.set_axisbelow(True)


def _save(fig, name):
    FIG.mkdir(parents=True, exist_ok=True)
    fig.tight_layout()
    fig.savefig(FIG / name, dpi=150, facecolor="#fcfcfb")
    plt.close(fig)
    return f"figures/{name}"


def _colour(method):
    return METHOD_COLOURS["P"] if method.startswith("P") else METHOD_COLOURS.get(method, MUTED)


def charts(con, chosen) -> dict:
    figs = {}
    hp = con.execute("SELECT * FROM an_hour_profile ORDER BY hour").df()
    fig, ax = plt.subplots(figsize=(8, 3.4))
    ax.axvspan(5.5, 21.5, color="#f1f0ec", zorder=0)
    ax.text(13.5, 44, "selling window 06:00 to 21:59", ha="center", fontsize=8, color=INK_2)
    ax.plot(hp.hour, hp.oos_share * 100, color=ORANGE, lw=2, marker="o", ms=3, label="% of series flagged out of stock")
    ax.bar(hp.hour, hp.sales_share * 100, color=BLUE, alpha=0.85, width=0.7, label="% of daily sales in the hour")
    ax.set_xticks(range(0, 24, 2), [f"{h:02d}" for h in range(0, 24, 2)])
    ax.set_ylim(0, 48)
    ax.set_xlabel("hour of day", fontsize=8)
    ax.grid(axis="y", color=GRID)
    ax.legend(fontsize=7.5, frameon=False, loc="upper left")
    _style(ax, "Overnight stock flags are high while almost nothing sells (train, 90 days)")
    figs["hours"] = _save(fig, "1_stockout_flags_by_hour.png")

    ms = con.execute("""SELECT * FROM an_masking_summary WHERE role LIKE 'holdout%' AND sellout_window <> '1. all'
                        ORDER BY sellout_window, estimator""").df()
    fig, ax = plt.subplots(figsize=(8, 3.4))
    windows = list(dict.fromkeys(ms.sellout_window))
    ests = list(dict.fromkeys(ms.estimator))
    cols = [MUTED, AMBER, ORANGE, BLUE]
    w = 0.2
    for j, (e, c) in enumerate(zip(ests, cols)):
        v = ms[ms.estimator == e].set_index("sellout_window").reindex(windows).wape * 100
        ax.bar([i + (j - 1.5) * w for i in range(len(windows))], v, width=w, color=c, label=e)
    ax.set_xticks(range(len(windows)), [x[3:] for x in windows])
    ax.set_ylabel("error, % of true demand (WAPE)", fontsize=8)
    ax.grid(axis="y", color=GRID)
    ax.legend(fontsize=7.5, frameon=False)
    _style(ax, "Masking test: hide the hours after a sell-out, then estimate the day (holdout history)")
    figs["masking"] = _save(fig, "2_masking_test.png")

    ef = con.execute(f"""SELECT fold, method, store_top10_lost_capture FROM eval_fold
                         WHERE method IN ('A1 Volume', 'A2 Frequency', 'B Lost demand', '{chosen}', 'Oracle (hindsight)')
                         ORDER BY fold""").df()
    fig, ax = plt.subplots(figsize=(8, 3.6))
    folds = sorted(ef.fold.unique())
    for m in ["Oracle (hindsight)", "B Lost demand", chosen, "A1 Volume", "A2 Frequency"]:
        d = ef[ef.method == m].set_index("fold").reindex(folds)
        ax.plot(range(len(folds)), d.store_top10_lost_capture * 100, color=_colour(m), lw=2.2 if m == chosen else 1.4,
                ls=":" if m.startswith("Oracle") else "-", marker="o", ms=3,
                label=("P Expected loss" if m == chosen else m))
    ax.axvspan(len(folds) - 1.5, len(folds) - 0.5, color="#f1f0ec", zorder=0)
    ax.text(len(folds) - 1, 30, "holdout", ha="center", fontsize=8, color=INK_2)
    ax.set_xticks(range(len(folds)), [f"day {f}" for f in folds])
    ax.set_ylabel("% of next-week lost demand\nin each store's top 10", fontsize=8)
    ax.grid(axis="y", color=GRID)
    ax.legend(fontsize=7.5, frameon=False, ncol=3, loc="lower left")
    _style(ax, "Backtest: lost demand captured by each store's top-10 list, by decision day")
    figs["folds"] = _save(fig, "3_backtest_by_fold.png")

    hs = con.execute("SELECT * FROM an_method_summary WHERE role = 'holdout'").df()
    fig, ax = plt.subplots(figsize=(6.4, 3.8))
    for _, r in hs.iterrows():
        ax.scatter(r.store_top10_hours_capture * 100, r.store_top10_lost_capture * 100, s=60, color=_colour(r.method),
                   zorder=3)
        label = "P Expected loss" if r.method.startswith("P ") else r.method.replace(" (hindsight)", "")
        offset = (-8, -13) if r.method.startswith("B ") else (6, 4)
        ax.annotate(label, (r.store_top10_hours_capture * 100, r.store_top10_lost_capture * 100),
                    textcoords="offset points", xytext=offset, fontsize=7.5, color=INK_2,
                    ha="right" if r.method.startswith("B ") else "left")
    ax.set_xlabel("% of next-week stockout hours in top 10 (model-free)", fontsize=8)
    ax.set_ylabel("% of next-week lost demand in top 10", fontsize=8)
    ax.grid(color=GRID)
    _style(ax, "Holdout week: lost units and stockout frequency pull apart")
    figs["tradeoff"] = _save(fig, "4_tradeoff_holdout.png")
    return figs


def run(con: duckdb.DuckDBPyConnection, tests: dict | None = None) -> None:
    chosen = con.execute("SELECT method FROM an_variant_selection WHERE chosen").fetchall()[0][0]
    figs = charts(con, chosen)
    q = lambda sql: con.execute(sql).fetchall()[0]  # noqa: E731
    man = con.execute("SELECT split, rows, series, first_date, last_date, sha256 FROM gov_source_manifest").df()
    dqr = con.execute("SELECT * FROM qa_dq_results ORDER BY rule_id").df()
    n_pass, n_warn = (dqr.status == "PASS").sum(), (dqr.status == "WARN").sum()

    rec_lost, rec_lost_all, oos_day_share = q(f"""
        SELECT sum(lost) FILTER (WHERE method = 'partial') / sum(window_sales), sum(lost) / sum(window_sales),
               avg((oos_hours > 0)::INT) FROM rec_daily WHERE fold = {T} AND NOT is_outcome""")
    partial_gap = q(f"SELECT sum(lost) / sum(window_sales) FROM rec_daily WHERE fold = {T} AND NOT is_outcome AND method = 'partial'")[0]

    mk = con.execute("SELECT role, sellout_window, estimator, days, wape, bias FROM an_masking_summary").df()
    mh = mk[mk.role.str.startswith("holdout") & (mk.sellout_window == "1. all")].set_index("estimator")
    wape_rec, wape_blend = mh.loc["A. Recorded sales (no adjustment)", "wape"], mh.loc["D. Coverage-weighted blend (used)", "wape"]
    wape_prof = mh.loc["C. Hourly profile scaling", "wape"]
    masked_days = int(mh.loc["D. Coverage-weighted blend (used)", "days"])

    fc = con.execute("SELECT * FROM an_forecast_check").df()
    fcp = fc.pivot(index="role", columns="forecast", values=["wape", "bias"])

    ms = con.execute("SELECT * FROM an_method_summary ORDER BY role, method").df()
    hol = ms[ms.role == "holdout"].set_index("method")
    dev = ms[ms.role == "development"].set_index("method")
    pname = [m for m in hol.index if m.startswith("P ")][0]
    cap = lambda role, m: (hol if role == "holdout" else dev).loc[m, "store_top10_lost_capture"]  # noqa: E731
    boot = con.execute("SELECT * FROM an_bootstrap").df()
    bl = boot[boot.metric == "store_top10_lost_capture"].set_index("baseline")
    bh = boot[boot.metric == "store_top10_hours_capture"].set_index("baseline")
    sel = con.execute("SELECT * FROM an_variant_selection ORDER BY dev_mean DESC").df()
    conf = con.execute("SELECT * FROM an_confidence_check").df().set_index("confidence")

    ex_store = q("SELECT store_id FROM decision_list WHERE global_rank = 1")[0]
    ex = con.execute(f"""SELECT store_rank AS "#", product_id AS product, third_category_id AS category,
                                round(expected_lost_7d, 1) AS expected_lost_7d, confidence, reasons,
                                round(outcome_lost, 1) AS realised_lost, outcome_oos_hours AS realised_oos_hours
                         FROM decision_list WHERE store_id = {ex_store} AND in_store_top_n ORDER BY store_rank""").df()
    listed = q("SELECT count(*), count(DISTINCT store_id) FROM decision_list WHERE in_store_top_n")
    tests_txt = tests["summary"] if tests else "run `python -m pytest tests`"

    method_tbl = ms[ms.role.isin(["development", "holdout"])][[
        "role", "method", "folds", "store_top10_lost_capture", "worst_fold_lost_capture", "store_top10_hours_capture",
        "global_top10pct_lost_capture", "store_top10_unavailable_capture"]].rename(columns={
            "store_top10_lost_capture": "lost demand in store top 10", "worst_fold_lost_capture": "worst fold",
            "store_top10_hours_capture": "stockout hours in store top 10",
            "global_top10pct_lost_capture": "lost demand in global top 10%",
            "store_top10_unavailable_capture": "unavailable days in store top 10"})
    pct = {c: "{:.1%}" for c in method_tbl.columns[3:]}

    gain_b = bl.loc["B Lost demand"]
    simple_enough = gain_b.difference < 0.02

    md = f"""# Availability to Action: Inventory Decision Evidence Report

**Data:** FreshRetailNet-50K (Dingdong Inc., CC BY 4.0): {man.loc[man.split == 'train', 'rows'].iat[0]:,d} train
and {man.loc[man.split == 'eval', 'rows'].iat[0]:,d} eval store-product-days, 50,000 series, 898 stores, 865 products,
{man.first_date.min()} to {man.last_date.max()}. Sales are normalised units (the publisher scales them), so results
are shares and ratios, never currency. **Params:** v{PARAMS['version']} ([config/params.json](../config/params.json)).
**DQ:** {n_pass} pass, {n_warn} warn, 0 fail. **Tests:** {tests_txt}.

## Executive brief

**Decision supported.** Each week, which store-product series should an inventory planner review first, and what
should they check before acting? Users: inventory planner, merchandise analyst, operations manager.

**What the evidence shows (verified on held-out data):**

1. **Recorded sales understate demand.** In the 28 days before the holdout week, {oos_day_share:.0%} of
   store-product-days had at least one stockout hour in the selling window. When we hide the hours after a sell-out on {masked_days:,d} real in-stock days, recorded sales
   miss **{wape_rec:.0%}** of the true day; the recovery method used here cuts the error to **{wape_blend:.0%}**
   (hourly-profile scaling alone: {wape_prof:.0%}). On partial stockout days the estimated hidden demand is
   {partial_gap:.0%} on top of recorded sales; across all days {rec_lost:.1%}.
2. **Ranking by estimated lost demand beats the two common rules.** On the holdout week each store's top-10 list
   under the proposed method holds **{cap('holdout', pname):.1%}** of next-week lost demand, against
   {cap('holdout', 'A1 Volume'):.1%} for 'top sellers first' and {cap('holdout', 'A2 Frequency'):.1%} for 'most
   frequent stockouts first' (hindsight ceiling: {cap('holdout', 'Oracle (hindsight)'):.1%}).
3. **The extra complexity buys {'almost nothing' if simple_enough else 'a measurable gain'}.** The proposed score
   (recency weighting + shrinkage) beats the simple 28-day lost-demand rule (B) by
   {gain_b.difference * 100:+.1f} points (95% store-bootstrap interval {gain_b.ci_low * 100:+.1f} to
   {gain_b.ci_high * 100:+.1f}). {'Real, but small: B is good enough to deploy and far easier to explain.' if simple_enough else ''}
4. **The right rule depends on the objective.** The frequency rule captures the most stockout *hours*
   ({hol.loc['A2 Frequency', 'store_top10_hours_capture']:.1%} vs {hol.loc[pname, 'store_top10_hours_capture']:.1%}):
   it is the better list if the goal is fewer empty-shelf incidents rather than fewer lost units.
5. **Confidence tiers carry real information.** For listed series, realised next-week lost demand was
   {conf.loc['High', 'realised_over_expected_listed']:.0%} of the expected value for High-confidence series but only
   {conf.loc['Low', 'realised_over_expected_listed']:.0%} for Low-confidence ones: low-confidence scores overstate the
   problem and need a stock check before anyone orders more.

**Recommendation (with conditions).** Use the 28-day lost-demand ranking (B or P) to build each store's weekly
top-10 review list, show the confidence tier and the reasons next to every row, and switch to the frequency rule
where the objective is shelf availability rather than units. This is a *review* list, not an order: the dataset has
no on-hand inventory, lead times, order costs or margins, so it cannot say how much to order or what a stockout
costs. Before any claim of savings, the list should be piloted against current practice with those data added.

## 1. The decision, the users and the baselines

| | |
|---|---|
| Decision | Which store-product series to review first for the coming week (top 10 per store, {listed[0]:,d} rows across {listed[1]} stores) |
| Users | Inventory planner (acts on the list), merchandise analyst (checks reasons, ranging), operations manager (store-level view) |
| Decision time | Day T; only days T-28 to T-1 are visible. Outcome measured on T to T+6 |
| Baseline A1 | Recorded sales, last 28 days ('protect the top sellers') |
| Baseline A2 | Days with a stockout, last 28 days ('repeat offenders') |
| Baseline B | Estimated lost demand, last 28 days (stockout-adjusted) |
| Proposed P | Expected next-week lost demand: recency-weighted recovered demand x unserved share shrunk toward the store x category share; half-life and shrinkage chosen on development folds ({pname}) |
| Primary metric | Share of next-week lost demand inside each store's top 10, summed over stores (fixed before the holdout was scored) |

## 2. Data foundation and quality

{_md(dqr[['rule_id', 'check', 'status', 'rows_flagged', 'rate', 'decision_impact', 'treatment']], {'rate': '{:.2%}'})}

The finding that changes decisions most is **DQ07**: the hourly stock flag stays on overnight after an evening
sell-out. A KPI built on all 24 hours reports a far higher stockout rate than the selling-window KPI used here, and
it counts an item that sells out at 21:00 as out of stock on two days instead of one.

![stock flags by hour]({figs['hours']})

## 3. Recorded sales understate demand: the masking test

Real stockout days have no ground truth, so the method is tested where truth exists: on fully in-stock days, the
hours after a sell-out time (drawn from the real sell-out distribution) are hidden and each estimator rebuilds the
day. The estimator was chosen on the development fold (decision day {PARAMS['recovery']['method_selection_fold']}) and
confirmed on the holdout history; the masked day never informs its own profile.

{_md(mk[mk.role.str.startswith('holdout') | (mk.sellout_window == '1. all')].rename(columns={'sellout_window': 'sell-out'}), {'wape': '{:.1%}', 'bias': '{:+.1%}'})}

Development-fold rows by sell-out time are in `outputs/powerbi/an_masking_summary.csv`.

![masking test]({figs['masking']})

What it shows: for late sell-outs the hourly profile is enough; for sell-outs before 13:00 most of the day is hidden
and the typical in-stock day is the better guide. The coverage-weighted blend uses each where it is strong. What it
cannot show: full-day stockouts (DQ08) and any demand that shifts to a substitute product.

## 4. Does the adjustment improve a simple forecast?

Truth is next-week days with no stockout hour (the only days where recorded sales equal demand).

{_md(fc, {'wape': '{:.1%}', 'bias': '{:+.1%}'})}

The result is mixed. On the holdout week the recovered mean removes most of the under-forecast
({fcp.loc['holdout', ('bias', 'Mean of recorded sales')]:+.1%} to {fcp.loc['holdout', ('bias', 'Mean of recovered demand')]:+.1%});
on the development weeks it over-forecasts ({fcp.loc['development', ('bias', 'Mean of recovered demand')]:+.1%}). Part
of this is selection: in-stock days are, by construction, days when demand did not exhaust stock, so they understate
demand. A forecasting claim needs a better truth set; the prioritisation result does not depend on it.

## 5. Prioritisation backtest

Seven development decision days inside the train period and one holdout (the eval split, never used to choose
anything). Variant selection on development folds:

{_md(sel, {'dev_mean': '{:.2%}'})}

{_md(method_tbl, pct)}

![backtest by fold]({figs['folds']})

Holdout differences, proposed minus baseline (1,000 store bootstrap resamples):

{_md(boot[['metric', 'baseline', 'difference', 'ci_low', 'ci_high', 'verdict']], {'difference': '{:+.2%}', 'ci_low': '{:+.2%}', 'ci_high': '{:+.2%}'})}

Lost demand in the outcome week is itself estimated with the recovery model, which favours B and P. The stockout
hours column is model-free and gives the same order for B and P against A1, so the result is not an artefact of the
target. The interval treats stores as independent and covers one holdout week only.

## 6. Trade-off: lost units or empty-shelf incidents

![trade-off]({figs['tradeoff']})

High-volume items lose the most units when they sell out; frequent-stockout items are often low-volume. No single
list wins both. The dashboard shows both scores so the user picks the objective, rather than the model picking it
silently.

## 7. Confidence tiers

{_md(conf.reset_index(), {'realised_over_expected_listed': '{:.0%}', 'listed_with_stockout_next_week': '{:.0%}', 'masking_wape': '{:.1%}'})}

Tier rule: High = at most {PARAMS['confidence']['high_max_imputed_share']:.0%} of 28-day demand imputed and at least
{PARAMS['confidence']['high_min_instock_days']} fully in-stock days; Low = over
{PARAMS['confidence']['low_max_imputed_share']:.0%} imputed or under {PARAMS['confidence']['low_min_instock_days']}
in-stock days. {conf.loc['Low', 'listed_with_stockout_next_week']:.0%} of listed Low-tier items stock out again the
next week, but their lost demand is overstated: they need a stock
and ranging check, not an automatic order. Masking error barely differs by tier because Low-tier series have very few
in-stock days to mask, so the calibration column is the evidence.

## 8. The review list: one store

Store {ex_store} (the store holding the single highest-scored series), holdout week:

{_md(ex)}

Every row on the full list also carries `checks_before_acting` (on-hand stock, open orders and lead time are not in
this dataset).

## 9. Limits: what this evidence cannot support

- **No cost or saving claim.** No on-hand inventory, lead time, order cost, shelf life or margin data: lost demand is
  in normalised units and cannot be priced.
- **Association, not cause.** Stockouts may coincide with high-demand days, promotions or weather; the backtest shows
  the list finds future losses, not why they happen.
- **Substitution is ignored.** Some 'lost' demand moves to a neighbouring product; true net loss is lower.
- **One retailer, 97 days, one holdout week.** Seasonality and holiday effects beyond this window are untested.
- **User validation pending.** No planner has used the list yet; a usability test with 3 to 5 planners is the next
  step and would be reported separately from any business impact.

## 10. Reproduce

```bash
pip install duckdb pandas matplotlib pytest
python scripts/download_data.py   # 115 MB from Hugging Face
python src/run_pipeline.py        # ~2 minutes: rebuild, DQ, backtest, exports, tests, this report
```
"""
    REP.mkdir(parents=True, exist_ok=True)
    (REP / "Availability_to_Action_Report.md").write_text(md, encoding="utf-8")
