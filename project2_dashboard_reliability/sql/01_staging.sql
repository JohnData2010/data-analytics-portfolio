-- Staging: type the two extracts, derive status groups and record-level QA flags.
-- The two datasets are NOT unioned: different systems, periods (2014-16 vs 2018),
-- taxonomies and status vocabularies. Each keeps its own staging table.

-- Extract date is not published. Assumption (logged in kpi_definitions.json):
-- extract date = latest date present anywhere in the file.
CREATE OR REPLACE TABLE ref_extract_dates AS
SELECT 'A' AS dataset,
       greatest(max(TRY_CAST(date_received AS DATE)), max(TRY_CAST(date_completed AS DATE))) AS extract_date
FROM raw_requests_a
UNION ALL
SELECT 'B',
       greatest(max(TRY_CAST(date_opened AS TIMESTAMPTZ)), max(TRY_CAST(date_closed AS TIMESTAMPTZ)))::DATE
FROM raw_requests_b;

-- Dataset A: Customer service requests with resolution time (2014-11 to 2016-11)
CREATE OR REPLACE TABLE stg_requests_a AS
WITH t AS (
    SELECT
        source_row,
        upper(trim(request_status))                         AS status_raw,
        date_received                                       AS date_received_raw,
        date_completed                                      AS date_completed_raw,
        TRY_CAST(date_received AS DATE)                     AS date_received,
        TRY_CAST(date_completed AS DATE)                    AS date_completed,
        nullif(trim(suburb), '')                            AS suburb,
        trim(category)                                      AS category,
        trim(service_desc)                                  AS service_desc,
        TRY_CAST(days_to_complete AS INTEGER)               AS days_field
    FROM raw_requests_a
)
SELECT t.*,
       CASE WHEN status_raw = 'CLOSED' THEN 'Closed' ELSE 'Open' END            AS status_group,
       status_raw = 'CLOSED'                                                     AS is_closed,
       date_completed - date_received                                            AS days_calendar,
       -- observed convention: same-day completion is recorded as 1 day
       greatest(date_completed - date_received, 1)                               AS days_calendar_min1,
       e.extract_date,
       CASE WHEN status_raw <> 'CLOSED' THEN e.extract_date - date_received END  AS age_at_extract,
       date_trunc('month', date_received)::DATE                                  AS intake_month,
       -- record-level QA flags
       date_received IS NULL                                                     AS dq_invalid_received,
       date_completed_raw IS NOT NULL AND date_completed IS NULL                 AS dq_invalid_completed,
       date_completed < date_received                                            AS dq_completed_before_received,
       date_received > e.extract_date OR date_completed > e.extract_date         AS dq_future_date,
       status_raw = 'CLOSED' AND date_completed IS NULL                          AS dq_closed_no_date,
       status_raw <> 'CLOSED' AND date_completed IS NOT NULL                     AS dq_open_with_date,
       suburb IS NULL                                                            AS dq_missing_suburb,
       status_raw = 'CLOSED' AND days_field <> greatest(date_completed - date_received, 1) AS dq_days_field_mismatch,
       status_raw <> 'CLOSED' AND days_field IS NOT NULL                         AS dq_days_field_on_open
FROM t CROSS JOIN (SELECT extract_date FROM ref_extract_dates WHERE dataset = 'A') e;

-- Dataset B: Salesforce subset with digital channel (2018-01 to 2018-09)
-- Timestamps are UTC; business days and months are assigned in Melbourne local time.
CREATE OR REPLACE TABLE ref_suburb_map AS
SELECT * FROM (VALUES
    ('MELBOURNE CBD', 'Melbourne'), ('MELB CBD', 'Melbourne'), ('MELBOURNE', 'Melbourne'),
    ('WEST MELB', 'West Melbourne'), ('NORTH MELB', 'North Melbourne'), ('EAST MELB', 'East Melbourne'),
    ('STH MELBOURNE', 'South Melbourne'), ('STH YARRA', 'South Yarra'),
    ('NTH MELBOURNE', 'North Melbourne'), ('KENSIGNTON', 'Kensington')
) AS m(variant, canonical);

-- Suburbs (wholly or partly) in the City of Melbourne municipality
CREATE OR REPLACE TABLE ref_com_suburbs AS
SELECT unnest(['Carlton', 'Carlton North', 'Docklands', 'East Melbourne', 'Flemington', 'Kensington',
               'Melbourne', 'North Melbourne', 'Parkville', 'Port Melbourne', 'Southbank', 'South Wharf',
               'South Yarra', 'West Melbourne']) AS suburb;

CREATE OR REPLACE MACRO title_case(s) AS
    array_to_string(list_transform(string_split(lower(s), ' '), lambda w: upper(w[1]) || w[2:]), ' ');

CREATE OR REPLACE TABLE stg_requests_b AS
WITH t AS (
    SELECT
        source_row,
        trim(service_type)                                   AS service_type,
        trim(status)                                         AS status_raw,
        trim(case_origin)                                    AS case_origin,
        nullif(trim(suburb), '')                             AS suburb_raw,
        TRY_CAST(date_opened AS TIMESTAMPTZ)                 AS opened_utc,
        TRY_CAST(date_closed AS TIMESTAMPTZ)                 AS closed_utc,
        date_opened                                          AS date_opened_raw,
        date_closed                                          AS date_closed_raw
    FROM raw_requests_b
)
SELECT t.*,
       timezone('Australia/Melbourne', opened_utc)                              AS opened_local,
       timezone('Australia/Melbourne', closed_utc)                              AS closed_local,
       timezone('Australia/Melbourne', opened_utc)::DATE                        AS date_opened,
       timezone('Australia/Melbourne', closed_utc)::DATE                        AS date_closed,
       CASE WHEN status_raw IN ('Closed', 'Closed as Duplicate') THEN 'Closed' ELSE 'Open' END AS status_group,
       status_raw IN ('Closed', 'Closed as Duplicate')                          AS is_closed,
       status_raw = 'Closed as Duplicate'                                        AS is_duplicate,
       CASE WHEN case_origin IN ('Web', 'Email', 'Snap Send Solve', 'Live Chat', 'SMS') THEN 'Digital'
            WHEN case_origin IN ('Phone', 'Face-to-Face', 'Mail') THEN 'Assisted'
            WHEN case_origin = 'Proactive' THEN 'Council-initiated'
            ELSE 'Unknown' END                                                    AS channel_group,
       coalesce(m.canonical, title_case(suburb_raw))                             AS suburb,
       epoch(closed_utc - opened_utc) / 86400.0                                  AS days_to_close,
       e.extract_date,
       CASE WHEN NOT status_raw IN ('Closed', 'Closed as Duplicate')
            THEN e.extract_date - timezone('Australia/Melbourne', opened_utc)::DATE END AS age_at_extract,
       date_trunc('month', timezone('Australia/Melbourne', opened_utc))::DATE   AS intake_month,
       date_trunc('month', opened_utc AT TIME ZONE 'UTC')::DATE                 AS intake_month_utc,
       opened_utc IS NULL                                                        AS dq_invalid_received,
       date_closed_raw IS NOT NULL AND closed_utc IS NULL                        AS dq_invalid_completed,
       closed_utc < opened_utc                                                   AS dq_completed_before_received,
       timezone('Australia/Melbourne', closed_utc)::DATE > e.extract_date        AS dq_future_date,
       status_raw IN ('Closed', 'Closed as Duplicate') AND closed_utc IS NULL    AS dq_closed_no_date,
       NOT status_raw IN ('Closed', 'Closed as Duplicate') AND closed_utc IS NOT NULL AS dq_open_with_date,
       suburb_raw IS NULL                                                        AS dq_missing_suburb,
       suburb_raw IS NOT NULL AND suburb_raw <> coalesce(m.canonical, title_case(suburb_raw)) AS dq_suburb_variant,
       suburb_raw IS NOT NULL
         AND coalesce(m.canonical, title_case(suburb_raw)) NOT IN (SELECT suburb FROM ref_com_suburbs) AS dq_suburb_outside_com
FROM t
LEFT JOIN ref_suburb_map m ON upper(t.suburb_raw) = m.variant
CROSS JOIN (SELECT extract_date FROM ref_extract_dates WHERE dataset = 'B') e;
