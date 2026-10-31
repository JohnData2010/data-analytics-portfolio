-- KPI layer (SQL). The Python KPI engine recomputes the same KPIs independently;
-- reconciliation compares the two ("computed correctly" check).

-- Common analysis view: one row per request, both datasets, explicit dataset label.
-- Used only for per-dataset KPIs; never aggregated across datasets.
CREATE OR REPLACE VIEW v_requests AS
SELECT 'A' AS dataset, source_row, category AS service_group, service_desc AS service_detail,
       status_raw, status_group, is_closed, FALSE AS is_duplicate,
       date_received, date_completed,
       CASE WHEN is_closed THEN days_field END AS completion_days,   -- published field, closed rows only
       days_calendar, age_at_extract, intake_month, extract_date, suburb
FROM stg_requests_a
UNION ALL
SELECT 'B', source_row, service_type, case_origin,
       status_raw, status_group, is_closed, is_duplicate,
       date_opened, date_closed,
       CASE WHEN is_closed THEN days_to_close END,
       date_closed - date_opened, age_at_extract, intake_month, extract_date, suburb
FROM stg_requests_b;

-- K01, K02, K04, K05 by dataset and service group (+ dataset total)
CREATE OR REPLACE TABLE kpi_sql_summary AS
SELECT dataset, coalesce(service_group, '(all)') AS service_group,
       count(*)                                                     AS requests,
       count(*) FILTER (WHERE is_closed)                            AS closed,
       count(*) FILTER (WHERE NOT is_closed)                        AS open,
       quantile_cont(completion_days, 0.5) FILTER (WHERE is_closed) AS k01_median_days,
       quantile_cont(completion_days, 0.9) FILTER (WHERE is_closed) AS k02_p90_days,
       quantile_cont(age_at_extract, 0.5) FILTER (WHERE NOT is_closed) AS k04_open_median_age,
       avg((age_at_extract > 90)::INT) FILTER (WHERE NOT is_closed) AS k04_open_share_over_90d,
       avg((NOT is_closed)::INT)                                    AS k05_open_share
FROM v_requests
GROUP BY GROUPING SETS ((dataset, service_group), (dataset));

-- K03 closure within 30 days, monthly intake cohorts with a full 30-day window
CREATE OR REPLACE TABLE kpi_sql_cohort AS
SELECT dataset, service_group, intake_month,
       count(*)                                                                        AS received,
       count(*) FILTER (WHERE is_closed AND completion_days <= 30)                     AS closed_within_30,
       count(*) FILTER (WHERE is_closed AND completion_days <= 30) / count(*)          AS k03_closure_30d,
       extract_date - last_day(intake_month)                                           AS followup_days_after_cohort_end,
       extract_date - last_day(intake_month) >= 30                                     AS full_window
FROM v_requests
GROUP BY dataset, service_group, intake_month, extract_date;

-- Transformation reconciliation: row counts at each step
CREATE OR REPLACE TABLE qa_row_lineage AS
SELECT 'A' AS dataset, 'raw' AS step, count(*) AS rows FROM raw_requests_a
UNION ALL SELECT 'A', 'staging', count(*) FROM stg_requests_a
UNION ALL SELECT 'A', 'analysis view', count(*) FROM v_requests WHERE dataset = 'A'
UNION ALL SELECT 'A', 'KPI population closed+open', sum(closed + open) FROM kpi_sql_summary WHERE dataset = 'A' AND service_group = '(all)'
UNION ALL SELECT 'A', 'cohort table', sum(received) FROM kpi_sql_cohort WHERE dataset = 'A'
UNION ALL SELECT 'B', 'raw', count(*) FROM raw_requests_b
UNION ALL SELECT 'B', 'staging', count(*) FROM stg_requests_b
UNION ALL SELECT 'B', 'analysis view', count(*) FROM v_requests WHERE dataset = 'B'
UNION ALL SELECT 'B', 'KPI population closed+open', sum(closed + open) FROM kpi_sql_summary WHERE dataset = 'B' AND service_group = '(all)'
UNION ALL SELECT 'B', 'cohort table', sum(received) FROM kpi_sql_cohort WHERE dataset = 'B';
