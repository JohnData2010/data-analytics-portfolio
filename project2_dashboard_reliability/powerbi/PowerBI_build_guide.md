# Power BI build guide: Dashboard Reliability Lab

Source: `outputs/powerbi/*.csv` (rewritten by every pipeline run). Power Query only sets types; all
logic lives in SQL/Python so that QA tests cover it.

## Model

| Table | Grain | Notes |
|---|---|---|
| `fact_requests_a` | request (A, 2014-16) | never related to B |
| `fact_requests_b` | request (B, 2018 subset) | local-time dates only |
| `kpi_summary`, `kpi_cohort` | dataset × service group (× month) | SQL KPI layer |
| `stress_kpi_values`, `stress_reversals`, `stress_rank_agreement` | dataset × group × KPI | KPI Stress Test page |
| `km_curves` | dataset × group × day | survival curves |
| `sensitivity_scenarios`, `sensitivity_trend` | scenario | Sensitivity page |
| `kpi_meaningfulness`, `kpi_integrity_recompute`, `kpi_period_comparability` | dataset × KPI | KPI Integrity page |
| `qa_dq_results`, `qa_row_lineage`, `gov_source_manifest` | rule / step / file | Data Quality page |
| `coverage_assessment`, `backlog_reconstruction`, `backlog_limitations` | finding / month | Coverage page |

A disconnected `Dataset` table (`A`, `B`) drives a single-select slicer; every visual filters on
`dataset`. Do not create a relationship between `fact_requests_a` and `fact_requests_b`.

## Measures

```DAX
Requests       = COUNTROWS ( fact_requests_a )            -- B page: same on fact_requests_b
Closed         = CALCULATE ( [Requests], fact_requests_a[is_closed] = TRUE () )
Open           = CALCULATE ( [Requests], fact_requests_a[is_closed] = FALSE () )
Open share     = DIVIDE ( [Open], [Requests] )

Median days (closed only) =
    CALCULATE ( MEDIAN ( fact_requests_a[days_field] ), fact_requests_a[is_closed] = TRUE () )
P90 days (closed only) =
    CALCULATE ( PERCENTILE.INC ( fact_requests_a[days_field], 0.9 ), fact_requests_a[is_closed] = TRUE () )
Open median age = CALCULATE ( MEDIAN ( fact_requests_a[age_at_extract] ), fact_requests_a[is_closed] = FALSE () )

Closed within 30d (full-window cohorts) =
    DIVIDE ( SUM ( kpi_cohort[closed_within_30] ), SUM ( kpi_cohort[received] ) )
    -- visual-level filter: kpi_cohort[full_window] = TRUE

KM median days = MAX ( stress_kpi_values[value] )   -- filter kpi = "K06"; computed in Python

-- KPI warning banner shown beside every median card
Median reliability note =
VAR m = SELECTEDVALUE ( kpi_meaningfulness[meaningful] )
RETURN IF ( m <> "Meaningful",
            "⚠ " & m & ": " & SELECTEDVALUE ( kpi_meaningfulness[reason] ) )

DQ checks failed = CALCULATE ( COUNTROWS ( qa_dq_results ), qa_dq_results[status] = "FAIL" )
DQ checks warned = CALCULATE ( COUNTROWS ( qa_dq_results ), qa_dq_results[status] = "WARN" )
Source file hash = "A " & LOOKUPVALUE ( gov_source_manifest[sha256], gov_source_manifest[dataset], "A" )
                 & " | B " & LOOKUPVALUE ( gov_source_manifest[sha256], gov_source_manifest[dataset], "B" )
```

## Pages

1. **Service Performance**: dataset slicer; cards: median, p90, KM median, open share, open median age;
   the `[Median reliability note]` banner sits directly under the median card.
2. **KPI Stress Test**: KPI selector (field parameter over the six KPIs); bar chart of the selected KPI by
   service group; rank matrix (group × KPI) from `stress_kpi_values`; table of `stress_reversals`.
3. **Survival**: `km_curves` step line, x = days, y = share closed, one line per dataset or group.
4. **Sensitivity**: `sensitivity_scenarios` table with conditional formatting on `slowest_changed`;
   `sensitivity_trend` with the comparability column always visible.
5. **Data Quality & Lineage**: `qa_dq_results` (status icon + label), `qa_row_lineage`, `gov_source_manifest`.
6. **Coverage & Limits**: `coverage_assessment`, backlog chart with the warm-up period shaded, `backlog_limitations`.

Every page footer: "Dataset B is a subset of council requests (Salesforce only). Latest record: A 08-Nov-2016, B 17-Oct-2018."
