"""
Слой Dimensions: заполнение справочников измерений.

Использует execute_values для пакетной загрузки.
"""

import logging

from psycopg2.extras import execute_values

from etl.utils import get_connection, BATCH_SIZE, BODY_GROUP_MAP

logger = logging.getLogger(__name__)


def load_dim_cities(conn) -> int:
    """
    Заполнение mart.dim_cities дедуплицированными городами.

    Дедупликация: для каждого gc_id выбирается строка с минимальным id.
    Также извлекается регион из full_name_local (текст после запятой).
    """
    cursor = conn.cursor()

    cursor.execute("""
        SELECT gc_id, id, full_name_local, full_name_gc, name, longitude, latitude
        FROM (
            SELECT gc_id, id, full_name_local, full_name_gc, name,
                   longitude, latitude,
                   ROW_NUMBER() OVER (PARTITION BY gc_id ORDER BY id) as rn
            FROM cities
        ) sub WHERE rn = 1
    """)

    rows = cursor.fetchall()

    dim_rows = []
    for row in rows:
        gc_id, city_id, full_name_local, full_name_gc, name, lon, lat = row

        # Извлечение региона из full_name_local (текст после запятой)
        region = None
        if full_name_local and "," in full_name_local:
            parts = full_name_local.split(",", 1)
            if len(parts) == 2 and parts[1].strip():
                region = parts[1].strip()

        dim_rows.append((gc_id, city_id, full_name_local, full_name_gc,
                         name, lon, lat, region))

    # Идемпотентно: удаляем старые, вставляем новые
    cursor.execute("DELETE FROM mart.dim_cities")
    if dim_rows:
        execute_values(
            cursor,
            """
            INSERT INTO mart.dim_cities
                (gc_id, city_id, full_name_local, full_name_gc, name,
                 longitude, latitude, region)
            VALUES %s
            """,
            dim_rows,
            page_size=BATCH_SIZE,
        )

    logger.info("Загружено %d городов в mart.dim_cities", len(dim_rows))
    return len(dim_rows)


def load_dim_body_groups(conn) -> int:
    """
    Заполнение mart.dim_body_groups унифицированными группами кузовов.
    """
    cursor = conn.cursor()

    cursor.execute("SELECT id, name, std_id FROM body_types")
    body_types = cursor.fetchall()

    dim_rows = []
    for bt_id, bt_name, std_id in body_types:
        if std_id is not None:
            body_group = BODY_GROUP_MAP.get(std_id, "Прочие")
        else:
            body_group = "Не указан"

        dim_rows.append((bt_id, body_group, std_id, bt_name))

    # Идемпотентно: удаляем старые, вставляем новые
    cursor.execute("DELETE FROM mart.dim_body_groups")
    if dim_rows:
        execute_values(
            cursor,
            """
            INSERT INTO mart.dim_body_groups
                (body_type_id, body_group, std_id, name)
            VALUES %s
            """,
            dim_rows,
            page_size=BATCH_SIZE,
        )

    logger.info("Загружено %d типов кузова в mart.dim_body_groups", len(dim_rows))
    return len(dim_rows)


def run_dimension_etl() -> dict:
    """Запуск ETL измерений для всех таблиц."""
    logger.info("Запуск ETL измерений")

    with get_connection() as conn:
        cities_count = load_dim_cities(conn)
        body_groups_count = load_dim_body_groups(conn)

    logger.info("ETL измерений завершён: %d городов, %d типов кузова",
                cities_count, body_groups_count)

    return {
        "cities_loaded": cities_count,
        "body_groups_loaded": body_groups_count,
    }
