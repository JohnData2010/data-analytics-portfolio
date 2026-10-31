# Project 1: Funding Is Not Access

NDIS support access and service-gap intelligence from NDIA public datasets (30-Sep-2024 to 30-Jun-2026).

**Question:** when funding is available, does it translate into support being used?

- **Findings:** [reports/NDIS_Funding_Access_Report.md](reports/NDIS_Funding_Access_Report.md)
- **Power BI build guide:** [powerbi/DAX_measures.md](powerbi/DAX_measures.md)
- **Reconciliation:** [outputs/reconciliation.csv](outputs/reconciliation.csv)
- **Evidence register:** [outputs/powerbi/an_evidence_register.csv](outputs/powerbi/an_evidence_register.csv)

## Run

```bash
pip install duckdb pandas openpyxl matplotlib pytest
python src/run_pipeline.py      # raw -> DuckDB -> marts -> QA -> analysis -> exports -> report (~1 min)
python -m pytest -q tests
```

Raw files are in `data/raw/` (downloaded from <https://dataresearch.ndis.gov.au/datasets/participant-datasets>).
To update, replace them with the new release (same file-name pattern) and rerun.

## Layout

| Path | Purpose |
|---|---|
| `src/ingest.py` | raw CSV/XLSX → DuckDB raw tables, values untouched |
| `sql/01_staging.sql` | typing, masking flags, label harmonisation, complementary-mask fallback |
| `sql/02_marts.sql` | one mart per grain; joins only between identical grains |
| `src/reconcile.py` | load, hierarchy and cross-source checks against the NDIA SDA supplement |
| `src/analysis.py` | persistence, mix standardisation, shift-share, SDA persistence, sensitivity, scenario grid |
| `src/evidence.py` | Module C rule engine: Observed / Requires investigation / Unsupported |
| `src/export.py`, `src/report.py` | Power BI CSVs; Markdown report + figures |
| `tests/` | invariants: no FAIL checks, masking never imputed, identities add up, guardrails hold |

## Metric definitions

| Metric | Definition | Grain |
|---|---|---|
| Utilisation | NDIA published: payments ÷ committed budget, supports in a 6-month window ending 3 months before the reporting date | date × district × support class (×disability×age) |
| Expected utilisation | Σ (district budget weight × national rate) over disability × age cells ÷ Σ weights | date × district |
| Mix effect | expected − national | date × district |
| Like-for-like gap | observed − expected | date × district |
| SDA eligible, not using | NDIA `SDA eligible - not yet using` | date × district |
| SDA in use | NDIA `SDA in use` | date × district |
| Non-use share | not using ÷ (in use + not using), same district and date, only when both are unmasked | date × district |
| Persistence | quarters (of 8) above national share / in bottom fifth | district |
| Scenario | participants moving = eligible total(30-Jun-2026) × reduction pts ÷ 100, capped at current non-users | district |

## Key decisions

- Masked cells (`<11`, complementary masks like `<12699`) are never imputed in published columns.
- Age bands changed in Dec-2024; trends use the harmonised `0 to 14` band.
- SDA dwellings (SA4) are never joined to service districts.
- A district is "robust" only if flagged by ≥3 of 5 alternative definitions; everything else is labelled definition-dependent.
