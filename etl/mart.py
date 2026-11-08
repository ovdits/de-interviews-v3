"""
Слой Mart: расчёт аналитической витрины mart.mart_route_card.

Использует SQL-оконные функции для расчёта перцентилей,
динамики MoM/YoY и уровня надёжности.
"""

import logging
from datetime import date

from etl.utils import get_connection

logger = logging.getLogger(__name__)


def calculate_route_card(conn, start_date: date, end_date: date,
                         batch_id: str, as_of: date = None) -> int:
    """
    Расчёт витрины «Карточка маршрута».

    Зерно: loading_month × loading_city_gcid × unloading_city_gcid
           × body_group × weight_category

    Метрики:
    - Рыночный коридор: P25, P50 (медиана), P75, AVG для рейса и км
    - Доверие: trips_count, carriers_count, cv_rate
    - Сезонность: MoM%, YoY% изменения P50
    - Надёжность: HIGH/MEDIUM/LOW_DATA
    """
    if as_of is None:
        as_of = end_date

    cursor = conn.cursor()

    # Удаляем старые данные за период
    cursor.execute(
        "DELETE FROM mart.mart_route_card "
        "WHERE loading_month >= %s AND loading_month < (%s + INTERVAL '1 month')::date",
        (start_date, end_date),
    )
    logger.info("Удалены старые данные карточки маршрута за %s — %s", start_date, end_date)

    # Вставляем свежие данные с перцентилями и агрегатами
    cursor.execute("""
        INSERT INTO mart.mart_route_card (
            loading_month, loading_city_gcid, unloading_city_gcid,
            body_group, weight_category,
            p25_trip, median_trip, p75_trip, avg_trip,
            p25_km, median_km, p75_km,
            trips_count, carriers_count, cv_rate,
            calculated_at, etl_batch_id
        )
        SELECT
            date_trunc('month', cp.loading_date)::date AS loading_month,
            cp.loading_city_gcid, cp.unloading_city_gcid,
            cp.body_group, cp.weight_category,

            PERCENTILE_CONT(0.25) WITHIN GROUP (ORDER BY cp.price_rub) AS p25_trip,
            PERCENTILE_CONT(0.50) WITHIN GROUP (ORDER BY cp.price_rub) AS median_trip,
            PERCENTILE_CONT(0.75) WITHIN GROUP (ORDER BY cp.price_rub) AS p75_trip,
            AVG(cp.price_rub) AS avg_trip,

            PERCENTILE_CONT(0.25) WITHIN GROUP (ORDER BY cp.price_rub / NULLIF(cp.distance, 0)) AS p25_km,
            PERCENTILE_CONT(0.50) WITHIN GROUP (ORDER BY cp.price_rub / NULLIF(cp.distance, 0)) AS median_km,
            PERCENTILE_CONT(0.75) WITHIN GROUP (ORDER BY cp.price_rub / NULLIF(cp.distance, 0)) AS p75_km,

            COUNT(*) AS trips_count,
            COUNT(DISTINCT CASE WHEN cp.price_type IN (3, 9) THEN cp.organization_id END) AS carriers_count,
            CASE
                WHEN COUNT(*) < 2 OR AVG(cp.price_rub) = 0 THEN NULL
                ELSE STDDEV_SAMP(cp.price_rub) / NULLIF(AVG(cp.price_rub), 0)
            END AS cv_rate,

            now(), %s
        FROM core.cleaned_prices cp
        WHERE cp.loading_date >= %s
          AND cp.loading_date <= %s
          AND cp.is_fact = true
          AND cp.complex = false
          AND cp.price_rub IS NOT NULL
          AND cp.price_rub > 0
          AND cp.distance > 0
          AND cp.outlier_mark = false
        GROUP BY
            date_trunc('month', cp.loading_date)::date,
            cp.loading_city_gcid, cp.unloading_city_gcid,
            cp.body_group, cp.weight_category
    """, (batch_id, start_date, end_date))

    logger.info("Вставлены данные в mart.mart_route_card")

    # Расчёт MoM и YoY изменений
    cursor.execute("""
        WITH mom AS (
            SELECT mc.id,
                ROUND(
                    (mc.median_trip - prev.median_trip) / NULLIF(prev.median_trip, 0) * 100,
                    2
                ) AS mom_pct
            FROM mart.mart_route_card mc
            LEFT JOIN mart.mart_route_card prev
                ON prev.loading_month = mc.loading_month - INTERVAL '1 month'
               AND prev.loading_city_gcid = mc.loading_city_gcid
               AND prev.unloading_city_gcid = mc.unloading_city_gcid
               AND prev.body_group = mc.body_group
               AND prev.weight_category = mc.weight_category
        ),
        yoy AS (
            SELECT mc.id,
                ROUND(
                    (mc.median_trip - prev.median_trip) / NULLIF(prev.median_trip, 0) * 100,
                    2
                ) AS yoy_pct
            FROM mart.mart_route_card mc
            LEFT JOIN mart.mart_route_card prev
                ON prev.loading_month = mc.loading_month - INTERVAL '1 year'
               AND prev.loading_city_gcid = mc.loading_city_gcid
               AND prev.unloading_city_gcid = mc.unloading_city_gcid
               AND prev.body_group = mc.body_group
               AND prev.weight_category = mc.weight_category
        )
        UPDATE mart.mart_route_card mc
        SET mom_pct = m.mom_pct, yoy_pct = y.yoy_pct
        FROM mom m
        JOIN yoy y ON y.id = m.id
        WHERE mc.id = m.id
    """)

    logger.info("Обновлены MoM/YoY для %d строк", cursor.rowcount)

    # Установка уровня надёжности
    cursor.execute("""
        UPDATE mart.mart_route_card
        SET reliability_level = CASE
            WHEN trips_count >= 30
                 AND carriers_count >= 5
                 AND (%s - loading_month) <= 30 THEN 'HIGH'
            WHEN trips_count >= 10 THEN 'MEDIUM'
            ELSE 'LOW_DATA'
        END
    """, (as_of,))

    logger.info("Установлен reliability_level для %d строк", cursor.rowcount)

    # Для INSERT ... SELECT rowcount может быть -1, поэтому считаем явно
    cursor.execute("SELECT COUNT(*) FROM mart.mart_route_card")
    inserted = cursor.fetchone()[0]

    return inserted


def run_mart_etl(start_date: date, end_date: date,
                 batch_id: str, as_of: date = None) -> dict:
    """Запуск ETL слоя витрин."""
    logger.info("Запуск ETL витрин за %s — %s", start_date, end_date)

    with get_connection() as conn:
        inserted = calculate_route_card(conn, start_date, end_date, batch_id, as_of)

    logger.info("ETL витрин завершён: %d карточек маршрутов", inserted)
    return {"route_card_rows": inserted}
