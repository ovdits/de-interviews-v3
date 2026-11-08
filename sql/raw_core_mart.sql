-- ============================================================================
-- DDL аналитического слоя (Raw / Core / Mart / DQ)
-- ============================================================================

-- 1. Создание схем
-- ============================================================================
CREATE SCHEMA IF NOT EXISTS raw;
CREATE SCHEMA IF NOT EXISTS core;
CREATE SCHEMA IF NOT EXISTS mart;
CREATE SCHEMA IF NOT EXISTS dq;

-- ============================================================================
-- СЛОЙ RAW: сырые данные из операционной БД (без трансформаций)
-- ============================================================================

-- 2. Raw-слой: сырые сделки из источника
-- ============================================================================
CREATE TABLE IF NOT EXISTS raw.raw_prices (
    id                  bigint PRIMARY KEY,
    created_at          timestamp(0),
    updated_at          timestamp(0),
    subscriber_id       integer,
    organization_id     integer,
    loading_date        date NOT NULL,
    loading_city_gcid   integer NOT NULL,
    unloading_city_gcid integer NOT NULL,
    distance            double precision NOT NULL,
    total_weight        double precision NOT NULL,
    total_volume        double precision NOT NULL,
    body_type           integer,
    price               double precision NOT NULL,
    currency_code       varchar(3) NOT NULL DEFAULT '643',
    complex             boolean NOT NULL DEFAULT false,
    price_type          smallint NOT NULL DEFAULT 2,
    item_id             integer,
    is_fact             boolean NOT NULL DEFAULT true,
    nearest_rate_date   date,
    meta_item_id        bigint,
    end_loading_date    date,
    source              varchar(255),
    external_item_uuid  uuid,
    outlier_mark        boolean NOT NULL DEFAULT false,
    weight_category     varchar(4),
    volume_category     varchar(4),
    price_group_id      bigint,
    extract_batch_id    varchar(50),
    extract_date        timestamp(0) NOT NULL DEFAULT now()
);

CREATE INDEX raw_prices_loading_date_idx ON raw.raw_prices (loading_date);
CREATE INDEX raw_prices_loading_unloading_idx ON raw.raw_prices (loading_city_gcid, unloading_city_gcid);
CREATE INDEX raw_prices_extract_batch_idx ON raw.raw_prices (extract_batch_id);

-- ============================================================================
-- СЛОЙ DQ: карантин отбракованных записей
-- ============================================================================

-- 3. DQ-карантин: отбракованные записи с кодом причины
-- ============================================================================
CREATE TABLE IF NOT EXISTS dq.dq_quarantine_prices (
    id              bigserial PRIMARY KEY,
    price_id        bigint NOT NULL,
    reason_code     varchar(50) NOT NULL,
    reason_detail   text,
    quarantined_at  timestamp(0) NOT NULL DEFAULT now(),
    batch_id        varchar(50)
);

CREATE INDEX dq_quarantine_price_id_idx ON dq.dq_quarantine_prices (price_id);
CREATE INDEX dq_quarantine_reason_idx ON dq.dq_quarantine_prices (reason_code);
CREATE INDEX dq_quarantine_batch_idx ON dq.dq_quarantine_prices (batch_id);

-- ============================================================================
-- СЛОЙ CORE: очищенные цены с RUB-конвертацией и обогащением
-- ============================================================================

-- 4. Core-слой: очищенные цены
-- Синхронизирован с ETL-кодом: body_group, weight_category, volume_category,
-- price_group_id включены в DDL
-- ============================================================================
CREATE TABLE IF NOT EXISTS core.cleaned_prices (
    id                  bigint PRIMARY KEY,
    created_at          timestamp(0),
    updated_at          timestamp(0),
    subscriber_id       integer,
    organization_id     integer,
    loading_date        date NOT NULL,
    loading_city_gcid   integer NOT NULL,
    unloading_city_gcid integer NOT NULL,
    distance            double precision NOT NULL,
    total_weight        double precision NOT NULL,
    total_volume        double precision NOT NULL,
    body_type           integer,
    price               double precision NOT NULL,
    currency_code       varchar(3) NOT NULL DEFAULT '643',
    price_rub           double precision,
    complex             boolean NOT NULL DEFAULT false,
    price_type          smallint NOT NULL DEFAULT 2,
    is_fact             boolean NOT NULL DEFAULT true,
    nearest_rate_date   date,
    outlier_mark        boolean NOT NULL DEFAULT false,
    weight_category     varchar(4),
    volume_category     varchar(4),
    price_group_id      bigint,
    body_group          varchar(50),
    etl_batch_id        varchar(50),
    etl_loaded_at       timestamp(0) NOT NULL DEFAULT now()
);

CREATE INDEX core_cleaned_prices_loading_date_idx ON core.cleaned_prices (loading_date);
CREATE INDEX core_cleaned_prices_loading_unloading_idx ON core.cleaned_prices (loading_city_gcid, unloading_city_gcid);
CREATE INDEX core_cleaned_prices_fact_idx ON core.cleaned_prices (is_fact, outlier_mark) WHERE is_fact = true AND outlier_mark = false;
CREATE INDEX core_cleaned_prices_batch_idx ON core.cleaned_prices (etl_batch_id);
CREATE INDEX core_cleaned_prices_body_group_idx ON core.cleaned_prices (body_group);
CREATE INDEX core_cleaned_prices_weight_cat_idx ON core.cleaned_prices (weight_category);

-- 5. ETL-метаданные: отслеживание батчей
-- ============================================================================
CREATE TABLE IF NOT EXISTS core.etl_runs (
    id               bigserial PRIMARY KEY,
    pipeline         varchar(50) NOT NULL,
    batch_id         varchar(50) NOT NULL,
    start_date       date,
    end_date         date,
    rows_read        integer DEFAULT 0,
    rows_written     integer DEFAULT 0,
    rows_quarantined integer DEFAULT 0,
    status           varchar(20) NOT NULL DEFAULT 'running',
    started_at       timestamp(0) NOT NULL DEFAULT now(),
    finished_at      timestamp(0),
    error_msg        text
);

CREATE INDEX etl_runs_batch_idx ON core.etl_runs (batch_id);
CREATE INDEX etl_runs_status_idx ON core.etl_runs (status);

-- ============================================================================
-- СЛОЙ MART: справочники (dim)
-- ============================================================================

-- 6. Mart-слой: справочник дедуплицированных городов (один на gc_id)
-- ============================================================================
CREATE TABLE IF NOT EXISTS mart.dim_cities (
    gc_id           integer PRIMARY KEY,
    city_id         bigint NOT NULL,
    full_name_local varchar(255) NOT NULL,
    full_name_gc    varchar(255) NOT NULL,
    name            varchar(255) NOT NULL,
    longitude       double precision,
    latitude        double precision,
    region          varchar(255),
    updated_at      timestamp(0) NOT NULL DEFAULT now()
);

CREATE INDEX dim_cities_region_idx ON mart.dim_cities (region);

-- 7. Mart-слой: справочник унифицированных групп кузовов
-- ============================================================================
CREATE TABLE IF NOT EXISTS mart.dim_body_groups (
    body_type_id integer PRIMARY KEY,
    body_group   varchar(50) NOT NULL,
    std_id       integer,
    name         varchar(255) NOT NULL
);

-- ============================================================================
-- СЛОЙ MART: аналитическая витрина «Карточка маршрута и коридор рыночных ставок»
-- ============================================================================

-- 8. Mart-слой: витрина «Карточка маршрута»
-- Grain: loading_month × loading_city_gcid × unloading_city_gcid × body_group × weight_category
-- ============================================================================
CREATE TABLE IF NOT EXISTS mart.mart_route_card (
    id                  bigserial PRIMARY KEY,
    loading_month         date NOT NULL,
    loading_city_gcid     integer NOT NULL,
    unloading_city_gcid   integer NOT NULL,
    body_group            varchar(50) NOT NULL,
    weight_category       varchar(4) NOT NULL,

    -- Рыночный коридор за рейс (руб./рейс)
    p25_trip              double precision,
    median_trip           double precision,
    p75_trip              double precision,
    avg_trip              double precision,

    -- Рыночный коридор за км (руб./км)
    p25_km                double precision,
    median_km             double precision,
    p75_km                double precision,

    -- Доказательная база
    trips_count           integer NOT NULL DEFAULT 0,
    carriers_count        integer,
    cv_rate               double precision,

    -- MoM / YoY изменения P50 (%)
    mom_pct               double precision,
    yoy_pct               double precision,

    -- Надёжность
    reliability_level     varchar(10) NOT NULL DEFAULT 'LOW_DATA',

    -- Метаданные
    calculated_at         timestamp(0) NOT NULL DEFAULT now(),
    etl_batch_id          varchar(50),

    PRIMARY KEY (loading_month, loading_city_gcid, unloading_city_gcid, body_group, weight_category)
);

CREATE INDEX mart_route_card_route_idx ON mart.mart_route_card (loading_city_gcid, unloading_city_gcid, body_group);
CREATE INDEX mart_route_card_month_idx ON mart.mart_route_card (loading_month);

-- 9. Mart-слой: предсказания ML-модели
-- ============================================================================
CREATE TABLE IF NOT EXISTS mart.ml_price_predictions (
    id                  bigserial PRIMARY KEY,
    price_id            bigint NOT NULL,
    loading_date        date NOT NULL,
    loading_city_gcid   integer NOT NULL,
    unloading_city_gcid integer NOT NULL,
    body_group          varchar(50),
    actual_price_rub    double precision,
    predicted_price_rub double precision,
    abs_error_rub       double precision,
    ape_pct             double precision,
    model_name          varchar(50) NOT NULL DEFAULT 'catboost_v1',
    scored_at           timestamp(0) NOT NULL DEFAULT now()
);

CREATE INDEX ml_predictions_price_id_idx ON mart.ml_price_predictions (price_id);
CREATE INDEX ml_predictions_loading_date_idx ON mart.ml_price_predictions (loading_date);
CREATE INDEX ml_predictions_model_idx ON mart.ml_price_predictions (model_name, scored_at);

-- ============================================================================
-- SQL: расчёт витрины mart.mart_route_card
-- Запускать после загрузки core.cleaned_prices (DELETE + INSERT витрины)
-- ============================================================================

-- 10. Процедура расчёта mart_route_card за указанный период
-- ============================================================================
CREATE OR REPLACE FUNCTION mart.calculate_route_card(
    p_start_date date,
    p_end_date date,
    p_batch_id varchar
) RETURNS void AS $$
DECLARE
    v_as_of date := p_end_date;
BEGIN
    -- Удаляем старые данные за период
    DELETE FROM mart.mart_route_card
    WHERE loading_month >= p_start_date
      AND loading_month < (p_end_date + interval '1 month')::date;

    -- Вставляем перцентили, агрегаты и доказательную базу
    INSERT INTO mart.mart_route_card (
        loading_month,
        loading_city_gcid,
        unloading_city_gcid,
        body_group,
        weight_category,
        p25_trip, median_trip, p75_trip, avg_trip,
        p25_km, median_km, p75_km,
        trips_count, carriers_count, cv_rate,
        calculated_at, etl_batch_id
    )
    SELECT
        date_trunc('month', cp.loading_date)::date AS loading_month,
        cp.loading_city_gcid,
        cp.unloading_city_gcid,
        cp.body_group,
        cp.weight_category,

        -- Рыночный коридор за рейс
        PERCENTILE_CONT(0.25) WITHIN GROUP (ORDER BY cp.price_rub) AS p25_trip,
        PERCENTILE_CONT(0.50) WITHIN GROUP (ORDER BY cp.price_rub) AS median_trip,
        PERCENTILE_CONT(0.75) WITHIN GROUP (ORDER BY cp.price_rub) AS p75_trip,
        AVG(cp.price_rub) AS avg_trip,

        -- Рыночный коридор за км
        PERCENTILE_CONT(0.25) WITHIN GROUP (ORDER BY cp.price_rub / NULLIF(cp.distance, 0)) AS p25_km,
        PERCENTILE_CONT(0.50) WITHIN GROUP (ORDER BY cp.price_rub / NULLIF(cp.distance, 0)) AS median_km,
        PERCENTILE_CONT(0.75) WITHIN GROUP (ORDER BY cp.price_rub / NULLIF(cp.distance, 0)) AS p75_km,

        -- Доказательная база
        COUNT(*) AS trips_count,
        COUNT(DISTINCT CASE WHEN cp.price_type IN (3, 9) THEN cp.organization_id END) AS carriers_count,
        CASE
            WHEN COUNT(*) < 2 OR AVG(cp.price_rub) = 0 THEN NULL
            ELSE STDDEV_SAMP(cp.price_rub) / NULLIF(AVG(cp.price_rub), 0)
        END AS cv_rate,

        now(),
        p_batch_id
    FROM core.cleaned_prices cp
    WHERE cp.loading_date >= p_start_date
      AND cp.loading_date <= p_end_date
      AND cp.is_fact = true
      AND cp.complex = false
      AND cp.price_rub IS NOT NULL
      AND cp.price_rub > 0
      AND cp.distance > 0
      AND cp.outlier_mark = false
    GROUP BY
        date_trunc('month', cp.loading_date)::date,
        cp.loading_city_gcid,
        cp.unloading_city_gcid,
        cp.body_group,
        cp.weight_category;

    -- Рассчитываем MoM и YoY изменения P50 через self-join
    -- LAG не гарантирует ровно 1 месяц / 12 месяцев при пропусках данных
    WITH mom AS (
        SELECT
            mc.id,
            ROUND(
                (mc.median_trip - prev.median_trip) / NULLIF(prev.median_trip, 0) * 100,
                2
            ) AS mom_pct
        FROM mart.mart_route_card mc
        LEFT JOIN mart.mart_route_card prev
            ON  prev.loading_month      = mc.loading_month - INTERVAL '1 month'
            AND prev.loading_city_gcid  = mc.loading_city_gcid
            AND prev.unloading_city_gcid = mc.unloading_city_gcid
            AND prev.body_group          = mc.body_group
            AND prev.weight_category     = mc.weight_category
    ),
    yoy AS (
        SELECT
            mc.id,
            ROUND(
                (mc.median_trip - prev.median_trip) / NULLIF(prev.median_trip, 0) * 100,
                2
            ) AS yoy_pct
        FROM mart.mart_route_card mc
        LEFT JOIN mart.mart_route_card prev
            ON  prev.loading_month      = mc.loading_month - INTERVAL '1 year'
            AND prev.loading_city_gcid  = mc.loading_city_gcid
            AND prev.unloading_city_gcid = mc.unloading_city_gcid
            AND prev.body_group          = mc.body_group
            AND prev.weight_category     = mc.weight_category
    )
    UPDATE mart.mart_route_card mc
    SET
        mom_pct = m.mom_pct,
        yoy_pct = y.yoy_pct
    FROM mom m
    JOIN yoy y ON y.id = m.id
    WHERE mc.id = m.id;

    -- Устанавливаем reliability_level с проверкой freshness последнего месяца
    -- HIGH: trips_count >= 30, carriers_count >= 5, последний месяц не старше 30 дней от as_of
    UPDATE mart.mart_route_card
    SET reliability_level = CASE
        WHEN trips_count >= 30
             AND carriers_count >= 5
             AND (v_as_of - loading_month) <= 30 THEN 'HIGH'
        WHEN trips_count >= 10 THEN 'MEDIUM'
        ELSE 'LOW_DATA'
    END;

END;
$$ LANGUAGE plpgsql;
