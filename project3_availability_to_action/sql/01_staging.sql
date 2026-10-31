-- Staging: one row per store-product-day (the source grain), train and eval stacked.
-- Selling window = hours 6..21 (list positions 7..22), the 16 hours behind stock_hour6_22_cnt.
-- Nothing is dropped here; quality issues become flags so every downstream count reconciles.

CREATE OR REPLACE TABLE stg_daily AS
WITH src AS (
    SELECT 'train' AS split, * FROM raw_train
    UNION ALL
    SELECT 'eval' AS split, * FROM raw_eval
)
SELECT
    store_id * 10000 + product_id                          AS series_id,
    store_id, product_id, city_id, management_group_id,
    first_category_id, second_category_id, third_category_id,
    split,
    CAST(dt AS DATE)                                        AS dt,
    CAST(dt AS DATE) - DATE '2024-03-28' + 1                AS day_idx,
    dayofweek(CAST(dt AS DATE))                             AS dow,
    sale_amount,
    list_slice(hours_sale, 7, 22)                           AS h,     -- hourly sales, selling window
    list_slice(hours_stock_status, 7, 22)                   AS s,     -- 1 = out of stock
    stock_hour6_22_cnt                                      AS oos_hours,
    list_sum(list_slice(hours_sale, 7, 22))                 AS window_sales,
    sale_amount - list_sum(list_slice(hours_sale, 7, 22))   AS offwindow_sales,
    list_sum(list_transform(list_slice(hours_sale, 7, 22),
        lambda x, i: CASE WHEN hours_stock_status[i + 6] = 0 THEN x ELSE 0 END)) AS instock_sales,
    list_position(list_slice(hours_stock_status, 7, 22), 1) + 5                  AS first_oos_hour,
    list_sum(hours_stock_status)                                                AS oos_hours_24h,
    hours_stock_status[1] = 1                                                   AS oos_at_midnight,
    discount, holiday_flag, activity_flag, precpt, avg_temperature, avg_humidity, avg_wind_level,
    -- quality flags (rules and their impact are in qa_dq_results)
    abs(list_sum(hours_sale) - sale_amount) > 1e-6                              AS dq_hourly_sum_mismatch,
    stock_hour6_22_cnt <> list_sum(list_slice(hours_stock_status, 7, 22))        AS dq_oos_count_mismatch,
    list_sum(list_transform(list_slice(hours_sale, 7, 22),
        lambda x, i: CASE WHEN hours_stock_status[i + 6] = 1 AND x > 0 THEN 1 ELSE 0 END)) > 0
                                                                                AS dq_sales_in_oos_hour,
    -- stricter: sales in an out-of-stock hour that follows another out-of-stock hour (not the sell-out hour)
    list_sum(list_transform(list_slice(hours_sale, 7, 22),
        lambda x, i: CASE WHEN hours_stock_status[i + 6] = 1 AND hours_stock_status[i + 5] = 1 AND x > 0
                          THEN 1 ELSE 0 END)) > 0                               AS dq_sales_after_sellout,
    stock_hour6_22_cnt = 16                                                     AS dq_full_day_oos,
    discount <= 0 OR discount > 1                                               AS dq_discount_out_of_range,
    sale_amount < 0                                                             AS dq_negative_sales
FROM src;

CREATE OR REPLACE TABLE dim_series AS
SELECT series_id, any_value(store_id) AS store_id, any_value(product_id) AS product_id,
       any_value(city_id) AS city_id, any_value(management_group_id) AS management_group_id,
       any_value(first_category_id) AS first_category_id, any_value(second_category_id) AS second_category_id,
       any_value(third_category_id) AS third_category_id
FROM stg_daily GROUP BY series_id;

CREATE OR REPLACE TABLE dim_date AS
SELECT DISTINCT dt, day_idx, dow, split, strftime(dt, '%a') AS weekday
FROM stg_daily ORDER BY dt;
