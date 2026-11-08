"""
Слой Raw: извлечение данных из источника в raw.raw_prices.

Первый этап пайплайна — загрузка «как есть» без трансформаций.
"""

import logging
import time
from datetime import date, datetime

from etl.utils import (
    get_connection, make_batch_id, save_etl_run,
    fmt_null, build_copy_sql, copy_from_strings,
    extract_prices,
)

logger = logging.getLogger(__name__)

# Столбцы raw.raw_prices — синхронизированы с DDL (raw_core_mart.sql:19-49)
RAW_COLUMNS = [
    "id", "created_at", "updated_at", "subscriber_id", "organization_id",
    "loading_date", "loading_city_gcid", "unloading_city_gcid",
    "distance", "total_weight", "total_volume", "body_type",
    "price", "currency_code", "complex", "price_type", "item_id",
    "is_fact", "nearest_rate_date", "meta_item_id", "end_loading_date",
    "source", "external_item_uuid", "outlier_mark",
    "weight_category", "volume_category", "price_group_id",
    "extract_batch_id", "extract_date",
]


def load_to_raw(conn, rows: list, batch_id: str) -> int:
    """
    Загрузка сырых данных в raw.raw_prices.

    Идемпотентно: DELETE+INSERT в одной транзакции.
    """
    cursor = conn.cursor()

    # Удаляем старые данные этого батча
    cursor.execute(
        "DELETE FROM raw.raw_prices WHERE extract_batch_id = %s",
        (batch_id,),
    )

    if not rows:
        logger.info("Нет строк для загрузки в raw")
        return 0

    # Формируем данные для COPY
    extract_date = datetime.now().isoformat()

    copy_rows = []
    for r in rows:
        # r содержит 22 поля из источника (индексы 0-21)
        copy_rows.append(
            f"{r[0]}\t{fmt_null(r[1])}\t{fmt_null(r[2])}\t{fmt_null(r[3])}\t{fmt_null(r[4])}\t"
            f"{r[5]}\t{r[6]}\t{r[7]}\t{fmt_null(r[8])}\t{fmt_null(r[9])}\t{fmt_null(r[10])}\t"
            f"{fmt_null(r[11])}\t{fmt_null(r[12])}\t{fmt_null(r[13])}\t{r[14]}\t{fmt_null(r[15])}\t"
            f"{fmt_null(r[16])}\t{r[17]}\t{fmt_null(r[18])}\t{fmt_null(r[19])}\t{fmt_null(r[20])}\t"
            f"{fmt_null(r[21])}\t{batch_id}\t{extract_date}"
        )

    copy_sql = build_copy_sql("raw.raw_prices", RAW_COLUMNS)
    copy_from_strings(cursor, copy_sql, copy_rows)

    logger.info("Загружено %d строк в raw.raw_prices (батч: %s)", len(copy_rows), batch_id)
    return len(copy_rows)


def run_raw_etl(start_date: date, end_date: date) -> dict:
    """
    Запуск ETL сырого слоя: источник -> raw.raw_prices.

    Возвращает словарь со статистикой.
    """
    batch_id = make_batch_id("raw", start_date, end_date)

    logger.info("=" * 60)
    logger.info("Raw ETL запущен (батч: %s)", batch_id)
    logger.info("Период: %s - %s", start_date, end_date)

    t0 = time.time()

    with get_connection() as conn:
        # Извлечение из источника
        prices = extract_prices(conn, start_date, end_date)

        # Загрузка в raw
        rows_loaded = load_to_raw(conn, prices, batch_id)

        # Сохранение метаданных
        save_etl_run(
            conn, "etl_raw", batch_id,
            start_date, end_date,
            rows_read=rows_loaded,
            rows_written=rows_loaded,
            rows_quarantined=0,
            status="success",
        )

    duration = time.time() - t0
    logger.info("Raw ETL завершён за %.1fs: %d строк", duration, rows_loaded)

    return {
        "batch_id": batch_id,
        "rows_loaded": rows_loaded,
        "duration_sec": round(duration, 2),
    }
