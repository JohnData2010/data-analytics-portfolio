# Power BI build guide: Funding-Utilisation Explorer & SDA Access Gap Monitor

The pipeline writes every model table to `outputs/powerbi/*.csv`. Power BI connects to that folder
(Get Data → Folder, or one Text/CSV query per file), so a pipeline rerun followed by **Refresh** updates
the report. No transformation logic lives in Power BI: Power Query only sets data types.

## 1. Model

| Table | Grain | Key(s) |
|---|---|---|
| `dim_period` | report date | `report_date` |
| `dim_district` | state × service district | `district_key` |
| `fact_funding_util_district` | date × district × support class | `report_date`, `district_key` |
| `fact_util_standardised` | date × district | `report_date`, `district_key` |
| `fact_util_cells` | date × district × disability × age | `report_date`, `district_key` |
| `fact_util_national` | date × support class | `report_date` |
| `fact_sda_district` | date × district | `report_date`, `district_key` |
| `fact_sda_sa4_context` | SA4 region, 30-Jun-2026 | **no relationship** (context only) |
| `an_util_persistence`, `an_sda_persistence` | district | `district_key` |
| `an_scenario_grid` | district × reduction pts | `district_key` |
| `an_evidence_register` | claim | `scope` (text; filter by slicer, no relationship) |
| `an_sensitivity_*`, `an_mix_decomposition`, `qa_reconciliation` | stand-alone | none |

Relationships: `dim_period[report_date]` 1→* each fact; `dim_district[district_key]` 1→* each district fact
and analysis table. Single direction. **Do not** relate `fact_sda_sa4_context` to `dim_district`:
SA4 regions are not service districts.

## 2. Measures

```DAX
-- ---------- Module A
Utilisation % =
VAR r = SELECTEDVALUE ( fact_funding_util_district[utilisation] )
RETURN IF ( ISBLANK ( r ), BLANK (), r )        -- published rates are not averaged across districts

National utilisation % =
CALCULATE ( MAX ( fact_util_national[utilisation] ),
            fact_util_national[support_class] = SELECTEDVALUE ( fact_funding_util_district[support_class], "ALL" ) )

Gap to national (pts) = ( [Utilisation %] - [National utilisation %] ) * 100

Expected utilisation % = AVERAGE ( fact_util_standardised[expected_rate] )
Mix effect (pts)        = AVERAGE ( fact_util_standardised[mix_effect] ) * 100
Like-for-like gap (pts) = AVERAGE ( fact_util_standardised[like_for_like_gap] ) * 100
Within-cell coverage %  = AVERAGE ( fact_util_standardised[within_cell_coverage] )

Active participants = SUM ( fact_funding_util_district[participants] )
Committed budget (est, $) = SUM ( fact_funding_util_district[committed_budget_est] )

Utilisation window =
VAR d = SELECTEDVALUE ( dim_period[report_date] )
RETURN "Supports delivered " & FORMAT ( LOOKUPVALUE ( dim_period[util_window_start], dim_period[report_date], d ), "d mmm yyyy" )
     & " to " & FORMAT ( LOOKUPVALUE ( dim_period[util_window_end], dim_period[report_date], d ), "d mmm yyyy" )

-- ---------- Module B
SDA in use            = SUM ( fact_sda_district[in_use] )
SDA eligible not using = SUM ( fact_sda_district[not_using] )
SDA eligible total    = SUM ( fact_sda_district[eligible_total] )
Non-use share %       = DIVIDE ( [SDA eligible not using], [SDA eligible total] )
National non-use share % = MAX ( fact_sda_district[national_non_use_share] )
Excess over national (pts) = ( [Non-use share %] - [National non-use share %] ) * 100
Masked cells in view  = CALCULATE ( COUNTROWS ( fact_sda_district ), fact_sda_district[any_masked] = TRUE () )
Quarters above national = MAX ( an_sda_persistence[quarters_above_national] )

-- ---------- Scenario (what-if parameter "Reduction pts", 0 to 20, step 1)
Scenario participants moving to use =
VAR pts = SELECTEDVALUE ( 'Reduction pts'[Reduction pts], 0 )
RETURN SUMX (
    FILTER ( an_scenario_grid, an_scenario_grid[reduction_pts] = 0 ),
    VAR moved = an_scenario_grid[denominator_eligible_total] * pts / 100
    RETURN IF ( moved > an_scenario_grid[baseline_not_using], an_scenario_grid[baseline_not_using], moved ) )

Scenario note = "Arithmetic only: eligible total fixed at 30 Jun 2026, all else unchanged. Not a forecast."

-- ---------- Module C
Observed patterns      = CALCULATE ( COUNTROWS ( an_evidence_register ), an_evidence_register[status] = "Observed pattern" )
Requires investigation = CALCULATE ( COUNTROWS ( an_evidence_register ), an_evidence_register[status] = "Requires investigation" )
Unsupported            = CALCULATE ( COUNTROWS ( an_evidence_register ), an_evidence_register[status] = "Unsupported conclusion" )
```

## 3. Pages

1. **Funding-Utilisation Explorer**: one row of slicers for reporting date, state and support class.
   Line: district vs national utilisation over 8 quarters. Table: district, active participants, utilisation,
   gap, mix effect, like-for-like gap, quarters in bottom fifth. Card: `[Utilisation window]`, always visible.
2. **Like-for-like**: scatter of raw gap vs like-for-like gap (`an_util_persistence`), drill-through to
   `fact_util_cells` for one district (disability × age heatmap of district rate − national rate).
3. **SDA Access Gap Monitor**: slicers for date and state. Columns: in use / eligible not using by district;
   line: non-use share vs national; matrix: quarters above national; card: `[Masked cells in view]`.
4. **Sensitivity**: matrix of `an_sensitivity_flags` (district × definition), `an_sensitivity_threshold`.
5. **Evidence & Scenario**: evidence register table with conditional status colour + icon,
   filtered by the district selected on page 3; what-if slider and `[Scenario participants moving to use]`
   with `[Scenario note]` beneath.
6. **Data quality**: `qa_reconciliation` with PASS/WARN/FAIL counts and notes.

Status colours (good/warning/critical) are reserved for PASS/WARN/FAIL and evidence status and always
ship with a text label.
