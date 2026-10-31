-- Marts: one table per analytical grain. Every mart states its grain in a comment
-- and only combines sources that share exactly that grain.

-- Grain: report_date x state x district x support_class (all other dims = ALL)
-- Joins budgets (point-in-time, active plans) with utilisation (6-month window,
-- 3-month lag). Same dimensional grain, different reference periods: the window
-- is carried in dim_period and must be shown wherever both appear together.
CREATE OR REPLACE TABLE mart_funding_util_district AS
SELECT
    u.report_date, u.state, u.district, u.state || ' | ' || u.district AS district_key,
    u.support_class,
    b.participants_filled AS participants,
    b.participants_source,
    b.avg_annualised_budget,
    b.participants_filled * b.avg_annualised_budget AS committed_budget_est,
    u.utilisation,
    u.utilisation_suppressed
FROM stg_utilisation u
LEFT JOIN stg_plan_budgets b
  ON  b.report_date = u.report_date AND b.state = u.state AND b.district = u.district
  AND b.support_class = u.support_class
  AND b.disability_group = 'ALL' AND b.age_band = 'ALL'
WHERE u.disability_group = 'ALL' AND u.age_band = 'ALL' AND u.sil_or_sda = 'ALL';

-- Grain: report_date x support_class (national reference rates)
CREATE OR REPLACE TABLE mart_util_national AS
SELECT report_date, support_class, utilisation
FROM stg_utilisation
WHERE state = 'ALL' AND district = 'ALL' AND disability_group = 'ALL'
  AND age_band = 'ALL' AND sil_or_sda = 'ALL';

-- Grain: report_date x state x district x disability_group x age_band
-- (support_class = ALL, SIL/SDA = ALL). Basis for like-for-like comparison.
-- Weight = committed budget dollars, because utilisation is a $ ratio
-- (payments / committed budget). Masked counts use half the published bound and
-- masked averages fall back to the national cell average; both are flagged.
CREATE OR REPLACE TABLE mart_util_cells AS
WITH nat AS (
    SELECT u.report_date, u.disability_group, u.age_band,
           u.utilisation AS nat_rate, b.avg_annualised_budget AS nat_avg_budget
    FROM stg_utilisation u
    LEFT JOIN stg_plan_budgets b
      ON b.report_date = u.report_date AND b.state = 'ALL' AND b.district = 'ALL'
     AND b.disability_group = u.disability_group AND b.age_band = u.age_band
     AND b.support_class = 'ALL'
    WHERE u.state = 'ALL' AND u.district = 'ALL' AND u.sil_or_sda = 'ALL'
      AND u.support_class = 'ALL' AND u.disability_group <> 'ALL' AND u.age_band <> 'ALL'
)
SELECT
    b.report_date, b.state, b.district, b.state || ' | ' || b.district AS district_key,
    b.disability_group, b.age_band, b.age_band_h,
    coalesce(b.participants_filled, b.participants_upper / 2.0)              AS participants_est,
    b.participants_filled IS NULL                                            AS participants_masked,
    coalesce(b.avg_annualised_budget, n.nat_avg_budget)                      AS avg_budget_est,
    b.avg_annualised_budget IS NULL                                          AS budget_imputed,
    coalesce(b.participants_filled, b.participants_upper / 2.0)
        * coalesce(b.avg_annualised_budget, n.nat_avg_budget)                AS weight_budget,
    u.utilisation                                                            AS district_rate,
    n.nat_rate
FROM stg_plan_budgets b
LEFT JOIN stg_utilisation u
  ON  u.report_date = b.report_date AND u.state = b.state AND u.district = b.district
  AND u.disability_group = b.disability_group AND u.age_band = b.age_band
  AND u.support_class = 'ALL' AND u.sil_or_sda = 'ALL'
LEFT JOIN nat n
  ON n.report_date = b.report_date AND n.disability_group = b.disability_group
 AND n.age_band = b.age_band
WHERE b.support_class = 'ALL' AND b.state <> 'ALL' AND b.district <> 'ALL'
  AND b.disability_group <> 'ALL' AND b.age_band <> 'ALL';

-- Grain: report_date x state x district. Indirect standardisation:
--   expected = what the district would show at national cell rates given its own mix
--   mix_effect = expected - national ; like_for_like_gap = observed - expected
--   within_cell_gap = budget-weighted mean of (district cell rate - national cell rate)
--   over cells where the district rate is published (coverage reported).
CREATE OR REPLACE TABLE mart_util_standardised AS
WITH agg AS (
    SELECT report_date, state, district, district_key,
           sum(weight_budget * nat_rate) FILTER (WHERE nat_rate IS NOT NULL)
             / sum(weight_budget) FILTER (WHERE nat_rate IS NOT NULL)           AS expected_rate,
           sum(weight_budget * (district_rate - nat_rate)) FILTER (WHERE district_rate IS NOT NULL AND nat_rate IS NOT NULL)
             / sum(weight_budget) FILTER (WHERE district_rate IS NOT NULL AND nat_rate IS NOT NULL) AS within_cell_gap,
           sum(weight_budget) FILTER (WHERE district_rate IS NOT NULL AND nat_rate IS NOT NULL)
             / sum(weight_budget)                                             AS within_cell_coverage,
           sum(weight_budget) FILTER (WHERE participants_masked OR budget_imputed)
             / sum(weight_budget)                                             AS weight_share_estimated
    FROM mart_util_cells
    GROUP BY ALL
)
SELECT a.*,
       f.utilisation      AS observed_rate,
       f.participants     AS active_participants,
       n.utilisation      AS national_rate,
       a.expected_rate - n.utilisation  AS mix_effect,
       f.utilisation - a.expected_rate  AS like_for_like_gap,
       f.utilisation - n.utilisation    AS raw_gap
FROM agg a
JOIN mart_funding_util_district f
  ON f.report_date = a.report_date AND f.district_key = a.district_key AND f.support_class = 'ALL'
JOIN mart_util_national n
  ON n.report_date = a.report_date AND n.support_class = 'ALL';

-- Grain: report_date x state x district. SDA status pivoted to columns.
CREATE OR REPLACE TABLE mart_sda_district AS
WITH p AS (
    SELECT report_date, state, district,
           max(participants)        FILTER (WHERE sda_status = 'SDA in use')                   AS in_use,
           bool_or(participants_masked) FILTER (WHERE sda_status = 'SDA in use')               AS in_use_masked,
           max(participants_upper)  FILTER (WHERE sda_status = 'SDA in use')                   AS in_use_upper,
           max(participants)        FILTER (WHERE sda_status = 'SDA eligible - not yet using') AS not_using,
           bool_or(participants_masked) FILTER (WHERE sda_status = 'SDA eligible - not yet using') AS not_using_masked,
           max(participants_upper)  FILTER (WHERE sda_status = 'SDA eligible - not yet using') AS not_using_upper
    FROM stg_sda_participants
    GROUP BY ALL
),
nat AS (
    SELECT report_date,
           sum(participants) FILTER (WHERE sda_status = 'SDA eligible - not yet using')
             / sum(participants)          AS national_non_use_share,
           sum(participants) FILTER (WHERE sda_status = 'SDA eligible - not yet using') AS national_not_using,
           sum(participants)              AS national_eligible
    FROM stg_sda_participants
    WHERE NOT participants_masked
    GROUP BY 1
)
SELECT p.*,
       p.state || ' | ' || p.district                     AS district_key,
       coalesce(p.in_use_masked, FALSE) OR coalesce(p.not_using_masked, FALSE) OR p.in_use IS NULL OR p.not_using IS NULL AS any_masked,
       p.in_use + p.not_using                             AS eligible_total,
       p.not_using / (p.in_use + p.not_using)             AS non_use_share,
       -- bounds when one side is masked ('<11' means 1..10)
       coalesce(p.not_using, 1) / (coalesce(p.not_using, 1) + coalesce(p.in_use, p.in_use_upper)) AS non_use_share_min,
       coalesce(p.not_using, p.not_using_upper) / (coalesce(p.not_using, p.not_using_upper) + coalesce(p.in_use, 1)) AS non_use_share_max,
       b.participants_filled                              AS active_participants,
       b.participants_source                              AS active_participants_source,
       1000.0 * p.not_using / b.participants_filled       AS not_using_per_1000_active,
       n.national_non_use_share, n.national_not_using, n.national_eligible
FROM p
LEFT JOIN stg_plan_budgets b
  ON  b.report_date = p.report_date AND b.state = p.state AND b.district = p.district
  AND b.disability_group = 'ALL' AND b.age_band = 'ALL' AND b.support_class = 'ALL'
LEFT JOIN nat n ON n.report_date = p.report_date;

-- Grain: SA4 region at 30-Jun-2026 only (SDA supplement P). SA4 is NOT a service
-- district: this table is context and is never joined to district marts.
CREATE OR REPLACE TABLE mart_sda_sa4_context AS
WITH need AS (
    SELECT region,
           TRY_CAST("Participants with SDA in use" AS INTEGER)                   AS in_use,
           TRY_CAST("Participants SDA eligible, not yet using SDA" AS INTEGER)   AS not_using,
           TRY_CAST("Total Participants with SDA in use or SDA eligible (not yet using SDA)" AS INTEGER) AS with_need
    FROM raw_sda_p9_need_sa4
),
res AS (
    SELECT region,
           TRY_CAST("Total" AS INTEGER) AS enrolled_dwellings,
           -- resident capacity lower bound: '6+' counted as 6
           TRY_CAST("1 Resident" AS INTEGER) * 1 + TRY_CAST("2 Residents" AS INTEGER) * 2
         + TRY_CAST("3 Residents" AS INTEGER) * 3 + TRY_CAST("4 Residents" AS INTEGER) * 4
         + TRY_CAST("5 Residents" AS INTEGER) * 5 + TRY_CAST("6+ Residents" AS INTEGER) * 6 AS resident_capacity_min
    FROM raw_sda_p6_dwellings_res
),
unf AS (
    SELECT region, TRY_CAST("Total" AS INTEGER) AS unfinished_new_build
    FROM raw_sda_p8_unfinished
)
SELECT coalesce(need.region, res.region) AS region,
       split_part(coalesce(need.region, res.region), ' - ', 1) AS state,
       position(' - ' IN coalesce(need.region, res.region)) > 0 AS is_sa4,
       need.in_use, need.not_using, need.with_need,
       res.enrolled_dwellings, res.resident_capacity_min, unf.unfinished_new_build,
       res.resident_capacity_min / NULLIF(need.with_need, 0) AS capacity_per_participant_with_need,
       DATE '2026-06-30' AS report_date
FROM need
FULL JOIN res ON res.region = need.region
LEFT JOIN unf ON unf.region = coalesce(need.region, res.region)
WHERE coalesce(need.region, res.region) NOT IN ('Total');
