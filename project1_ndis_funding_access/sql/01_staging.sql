-- Staging: type the raw NDIA text, keep masking explicit, harmonise labels.
-- Rule: never impute a masked value here. Masked cells get value NULL,
-- is_masked = TRUE and the published upper bound (e.g. '<11' -> 10).

CREATE OR REPLACE MACRO parse_rprt(d) AS strptime(upper(d), '%d%b%Y')::DATE;
CREATE OR REPLACE MACRO masked_upper(v) AS
    CASE WHEN v LIKE '<%' THEN TRY_CAST(replace(v, '<', '') AS INTEGER) - 1 END;
CREATE OR REPLACE MACRO to_int(v) AS TRY_CAST(replace(v, ',', '') AS INTEGER);
CREATE OR REPLACE MACRO supp_class(v) AS
    CASE WHEN v IN ('CapacityBuilding', 'Capacity Building') THEN 'Capacity Building' ELSE v END;
-- Age bands changed in Dec-2024 (0-6/7-14 -> 0-8/9-14). '0 to 14' is the only
-- band that is comparable across all 8 quarters.
CREATE OR REPLACE MACRO age_harmonised(v) AS
    CASE WHEN v IN ('0 to 6', '7 to 14', '0 to 8', '9 to 14') THEN '0 to 14' ELSE v END;
CREATE OR REPLACE MACRO quarter_label(d) AS
    CASE WHEN month(d) >= 7 THEN year(d) || '-' || right((year(d) + 1)::VARCHAR, 2)
         ELSE (year(d) - 1) || '-' || right(year(d)::VARCHAR, 2) END
    || ' Q' || CASE month(d) WHEN 9 THEN 1 WHEN 12 THEN 2 WHEN 3 THEN 3 ELSE 4 END;

CREATE OR REPLACE TABLE stg_plan_budgets_base AS
SELECT
    parse_rprt(RprtDt)                              AS report_date,
    StateCd                                         AS state,
    SrvcDstrctNm                                    AS district,
    DsbltyGrpNm                                     AS disability_group,
    AgeBnd                                          AS age_band,
    age_harmonised(AgeBnd)                          AS age_band_h,
    supp_class(SuppClass)                           AS support_class,
    to_int(ActvPrtcpnt)                             AS participants,
    ActvPrtcpnt LIKE '<%'                           AS participants_masked,
    coalesce(to_int(ActvPrtcpnt), masked_upper(ActvPrtcpnt)) AS participants_upper,
    TRY_CAST(replace(AvgAnlsdCmtdSuppBdgt, ',', '') AS DOUBLE) AS avg_annualised_budget,
    source_file
FROM raw_plan_budgets;

-- Complementary masking: NDIA masks e.g. ACT|ACT as '<12699' so that the tiny
-- NSW|ACT cross-border cell cannot be derived. The state=ALL row for the same
-- district is published unmasked and differs only by those few cross-border
-- participants (bounded by the mask). District names are unique except 'Other',
-- so the fallback is used only for named districts, only when the state=ALL
-- value is consistent with the mask bound (rules out e.g. NSW|ACT '<11'), and is flagged.
CREATE OR REPLACE TABLE stg_plan_budgets AS
SELECT s.*,
       coalesce(s.participants,
                CASE WHEN s.state <> 'ALL' AND s.district NOT IN ('ALL', 'Other')
                      AND a.participants <= s.participants_upper
                     THEN a.participants END)                       AS participants_filled,
       CASE WHEN s.participants IS NOT NULL THEN 'published'
            WHEN s.state <> 'ALL' AND s.district NOT IN ('ALL', 'Other')
                 AND a.participants <= s.participants_upper
                 THEN 'district total (state=ALL) - complementary mask'
            ELSE 'masked' END                                       AS participants_source
FROM stg_plan_budgets_base s
LEFT JOIN stg_plan_budgets_base a
  ON  a.report_date = s.report_date AND a.state = 'ALL' AND s.state <> 'ALL'
  AND a.district = s.district AND a.disability_group = s.disability_group
  AND a.age_band = s.age_band AND a.support_class = s.support_class;

CREATE OR REPLACE TABLE stg_utilisation AS
SELECT
    parse_rprt(RprtDt)                              AS report_date,
    StateCd                                         AS state,
    SrvcDstrctNm                                    AS district,
    DsbltyGrpNm                                     AS disability_group,
    AgeBnd                                          AS age_band,
    age_harmonised(AgeBnd)                          AS age_band_h,
    SILorSDA                                        AS sil_or_sda,
    supp_class(SuppClass)                           AS support_class,
    TRY_CAST(replace(Utlstn, '%', '') AS DOUBLE) / 100 AS utilisation,
    Utlstn IS NULL OR Utlstn = ''                   AS utilisation_suppressed,
    source_file
FROM raw_utilisation;

-- SDA file labels the residual district '<STATE> - Other'; budgets/utilisation use 'Other'.
CREATE OR REPLACE TABLE stg_sda_participants AS
SELECT
    parse_rprt(RprtDt)                              AS report_date,
    CASE WHEN StateCd = 'Mis' THEN 'MIS' ELSE StateCd END AS state,
    SrvcDstrctDesc                                  AS district_source,
    CASE WHEN SrvcDstrctDesc LIKE '% - Other' THEN 'Other' ELSE SrvcDstrctDesc END AS district,
    SDAStatus                                       AS sda_status,
    to_int(SDA_participants_masked)                 AS participants,
    SDA_participants_masked LIKE '<%'               AS participants_masked,
    coalesce(to_int(SDA_participants_masked), masked_upper(SDA_participants_masked)) AS participants_upper,
    source_file
FROM raw_sda_participants;

CREATE OR REPLACE TABLE dim_period AS
SELECT DISTINCT report_date,
       quarter_label(report_date) AS fy_quarter,
       report_date >= DATE '2024-12-31' AS new_age_bands,
       -- utilisation looks at supports delivered in the 6 months ending 3 months before report_date
       (last_day(report_date - INTERVAL 9 MONTH) + INTERVAL 1 DAY)::DATE AS util_window_start,
       last_day(report_date - INTERVAL 3 MONTH)::DATE AS util_window_end
FROM stg_plan_budgets
ORDER BY report_date;

CREATE OR REPLACE TABLE dim_district AS
SELECT DISTINCT state, district, state || ' | ' || district AS district_key
FROM (
    SELECT state, district FROM stg_plan_budgets
    UNION SELECT state, district FROM stg_utilisation
    UNION SELECT state, district FROM stg_sda_participants
)
WHERE district <> 'ALL' AND state <> 'ALL';
