# Project Requirements Document: Availability to Action

Version 1.0, 31-Oct-2026. Status of every acceptance criterion is at the end, with the evidence that settles it.

## 1. Business question

> How can a retailer identify inventory risks, prioritise interventions and evaluate those decisions when recorded
> sales do not fully reflect customer demand?

Recorded sales stop when the shelf is empty. A planner who ranks items by recorded sales, or by how often they
stock out, is ranking on a censored signal. This project tests whether correcting for that censoring produces a
better review list, and says plainly where it does not.

## 2. Users and the decision

| User | Decision | What they need from the system |
|---|---|---|
| Inventory planner | Which store-product series to review first in the coming week | A short ranked list per store, a reason for each row, a confidence flag |
| Merchandise analyst | Whether a repeated stockout is a supply, ranging or data problem | Evidence behind each rank: stockout days, sell-out hour, unavailable days, data conflicts |
| Operations manager | Which stores carry the most hidden demand | Store-level recorded vs recovered demand and stockout share |

Decision cadence: weekly. Decision time T: only days T-28 to T-1 are visible. Output: top 10 series per store.

## 3. Scope

**In:** data quality and lineage; selling-window availability KPIs; censored demand recovery with a validation
test; three ranking rules plus a proposed score; rolling-origin backtest with an untouched holdout week; confidence
tiers; reasons and checks per row; Power BI model and report; automated tests; decision evidence report.

**Out (and why):** order quantities and replenishment optimisation (no on-hand stock, lead time, order cost or
shelf life in the data); cost or saving estimates (sales are normalised units); causal effect of promotions or
weather on stockouts (observational data, no experiment); substitution between products.

## 4. Data scope

| Item | Value |
|---|---|
| Source | FreshRetailNet-50K, Dingdong Inc., Hugging Face, CC BY 4.0 (Wang et al., 2025, arXiv:2505.16319) |
| Grain | One row per store, product and day, with 24 hourly sales values and 24 hourly stock flags |
| Size | 4,500,000 train rows (28-Mar to 25-Jun-2024) + 350,000 eval rows (26-Jun to 02-Jul-2024); 50,000 series, 898 stores, 865 products, 18 cities |
| Covariates (not used in scores) | Discount, holiday and activity flags, precipitation, temperature, humidity, wind |
| Not available | On-hand inventory, orders, lead times, costs, prices, margins, shelf life |

## 5. Functional requirements

| ID | Requirement | Where |
|---|---|---|
| F1 | Load source files with a hash manifest; one command rebuilds everything | `src/ingest.py`, `src/run_pipeline.py` |
| F2 | Data quality rules with rows flagged, decision impact and treatment; a critical failure stops the run | `src/dq.py`, `qa_dq_results` |
| F3 | Availability KPIs on the selling window (06:00 to 21:59) only | `sql/01_staging.sql`, DQ04, DQ07 |
| F4 | Recover demand on stockout days using only history before the decision day | `src/recovery.py` |
| F5 | Validate recovery where truth exists (masking test), choose the method on a development fold | `an_masking_summary` |
| F6 | Score every series with baselines A1, A2, B and proposed P | `src/prioritise.py` |
| F7 | Backtest on 7 development decision days; choose P's settings there; score the holdout once | `src/evaluate.py` |
| F8 | Uncertainty on the holdout difference (store bootstrap) | `an_bootstrap` |
| F9 | Review list with rank, confidence tier, reasons and checks before acting | `decision_list` |
| F10 | Power BI star schema and a report that opens on the review list | `outputs/powerbi/`, `powerbi/` |
| F11 | Automated tests for integrity, calculations, leakage and wording | `tests/test_pipeline.py` |
| F12 | Decision evidence report generated from the tables, no typed numbers | `src/report.py` |

## 6. Baselines and proposed method

| Method | Score at decision day T | Question it answers |
|---|---|---|
| A1 Volume | Recorded sales, last 28 days | How far does 'protect the top sellers' get? |
| A2 Frequency | Days with a stockout, last 28 days | How far does 'fix the repeat offenders' get? |
| B Lost demand | Recovered minus recorded demand, last 28 days | Does correcting for censoring help? |
| P Expected loss | 7 x recency-weighted recovered demand x unserved share, shrunk toward store x category | Do recency and shrinkage add anything over B? |

B is nested in P (no recency weighting, no shrinkage), so P can only be chosen if it beats B on development data.

## 7. Evaluation plan

- **Analytical performance.** Primary metric, fixed before the holdout was scored: share of next-week lost demand on
  partial stockout days that falls inside each store's top 10, summed over stores. Secondary: share of next-week
  stockout hours (model-free), global top 10%, unavailable days. Masking WAPE and bias for recovery. Forecast WAPE
  and bias on in-stock days.
- **No leakage.** Features use days T-28 to T-1 only; the outcome week uses the history profile; the masked day is
  left out of its own profile; P's settings and the recovery method are chosen on development folds. Each rule is a
  test.
- **Decision usefulness.** Confidence tiers are checked against realised outcomes (realised / expected lost demand
  for listed series). Planner usability test: see criterion AC7.
- **Operational reliability.** One-command rebuild, DQ rules that stop the run on critical failure, row lineage,
  versioned parameters, append-only QA log.

## 8. Acceptance criteria

| # | Criterion | Status | Evidence |
|---|---|---|---|
| AC1 | Business problem: a specific decision, specific users, named baselines | Met | Sections 2 and 6; report section 1 |
| AC2 | Data integrity: sourced, grain and panel checked, definitions documented | Met | `qa_dq_results` (6 pass, 4 warn with treatment), `docs/metric_dictionary.md`, source manifest |
| AC3 | Analytical rigour: evaluated outside development data, leakage tested | Met | Holdout week = eval split; `test_features_use_only_days_before_decision`, `test_holdout_is_eval_split_and_not_used_for_selection` |
| AC4 | Decision evidence: each recommendation with evidence, limits and trade-offs | Met | Report: executive brief, sections 5 to 7 and 9 |
| AC5 | Business communication: executive brief and a dashboard for non-technical users | Met (Power BI not yet opened by a user) | Report executive brief; `powerbi/Availability_to_Action.pbip`, review list page |
| AC6 | Reproducibility: someone else can rerun and check | Met | `scripts/download_data.py` + `src/run_pipeline.py`; 14 automated tests; file hashes in the QA log |
| AC7 | User validation: feedback from trial users, representativeness stated | **Not met** | Planned: 3 to 5 people with store or inventory experience, task-based test on the review list page |

## 9. Risks and what would change the recommendation

- If a pilot with on-hand stock data shows Low-confidence rows are mostly phantom inventory or ranging changes,
  the list should exclude them by default rather than flag them.
- If the business objective is shelf availability (incidents) rather than units, A2 is the better list.
- If the gap between P and B stays under one point in a longer backtest, deploy B: it is easier to explain and audit.
