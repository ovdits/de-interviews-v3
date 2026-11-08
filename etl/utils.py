"""
Общие утилиты для ETL-пайплайна.

Общие константы, функции управления соединениями и форматирования,
используемые во всех модулях (raw, core, dimensions, mart).
"""

import io
import logging
import time
import uuid
from contextlib import contextmanager
from datetime import date, datetime

import psycopg2
from psycopg2.extras import execute_values, RealDictCursor

from etl.config import DB_CONFIG, BATCH_SIZE, LOG_FORMAT

logger = logging.getLogger(__name__)

# Группа кузова: std_id -> название
BODY_GROUP_MAP = {
    56: "Тент",
    4: "Рефрижератор",
    2: "Изотерм",
    15: "Борт",
}
OTHER_BODY_GROUP = "Прочие"
UNASSIGNED_BODY_GROUP = "Не указан"


# ===================================================================
# Управление соединениями
# ===================================================================

@contextmanager
def get_connection(retries: int = 3, timeout_sec: int = 30):
    """
    Контекстный менеджер для соединений с БД (авто-commit/rollback).

    Args:
        retries: количество попыток подключения
        timeout_sec: таймаут подключения в секундах
    """
    last_error = None
    for attempt in range(1, retries + 1):
        try:
            conn = psycopg2.connect(
                **DB_CONFIG,
                connect_timeout=timeout_sec,
            )
            logger.info(
                "Подключено к PostgreSQL: %s:%s/%s (попытка %d/%d)",
                DB_CONFIG["host"], DB_CONFIG["port"], DB_CONFIG["dbname"],
                attempt, retries,
            )
            try:
                yield conn
                conn.commit()
            except Exception:
                conn.rollback()
                raise
            finally:
                conn.close()
            return  # Успешно — выходим
        except psycopg2.OperationalError as e:
            last_error = e
            logger.warning(
                "Ошибка подключения к БД (попытка %d/%d): %s",
                attempt, retries, e,
            )
            if attempt < retries:
                time.sleep(min(2 ** attempt, 10))  # Exponential backoff

    raise last_error  # type: ignore[misc]


# ===================================================================
# Вспомогательные функции для COPY
# ===================================================================

def fmt_null(val) -> str:
    """Форматирование значения для PostgreSQL COPY: None -> (NULL marker)."""
    if val is None:
        return r"\N"
    if isinstance(val, datetime):
        return val.isoformat()
    return str(val)


def build_copy_sql(table: str, columns: list) -> str:
    """Формирует SQL-запрос COPY ... FROM STDIN."""
    cols = ", ".join(columns)
    return f"COPY {table} ({cols}) FROM STDIN WITH (FORMAT text, NULL '\\N')"


def copy_from_strings(cursor, sql: str, rows: list) -> None:
    """Пакетная загрузка через copy_expert из списка строк."""
    if rows:
        cursor.copy_expert(sql, io.StringIO("\n".join(rows)))


# ===================================================================
# Генерация ID батча
# ===================================================================

def make_batch_id(prefix: str, start_date: date, end_date: date) -> str:
    """Генерация уникального ID батча с префиксом."""
    return f"{prefix}_{start_date}_{end_date}_{uuid.uuid4().hex}_{int(time.time())}"


# ===================================================================
# Метаданные запусков
# ===================================================================

def save_etl_run(conn, pipeline: str, batch_id: str,
                 start_date: date, end_date: date,
                 rows_read: int, rows_written: int,
                 rows_quarantined: int, status: str,
                 error_msg: str = None) -> None:
    """Сохранение метаданных запуска ETL в core.etl_runs."""
    cursor = conn.cursor()
    cursor.execute(
        """
        INSERT INTO core.etl_runs
            (pipeline, batch_id, start_date, end_date,
             rows_read, rows_written, rows_quarantined, status,
             started_at, finished_at, error_msg)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, now(), now(), %s)
        """,
        (pipeline, batch_id, start_date, end_date,
         rows_read, rows_written, rows_quarantined, status, error_msg),
    )
    logger.info("Метаданные ETL сохранены: %s (батч: %s, статус: %s)",
                pipeline, batch_id, status)


# ===================================================================
# Извлечение данных из источника
# ===================================================================

def extract_prices(conn, start_date: date, end_date: date) -> list:
    """Извлечение сырых сделок из источника за указанный период."""
    query = """
        SELECT id, created_at, updated_at, subscriber_id, organization_id,
               loading_date, loading_city_gcid, unloading_city_gcid,
               distance, total_weight, total_volume, body_type,
               price, currency_code, complex, price_type, item_id,
               is_fact, nearest_rate_date, meta_item_id, end_loading_date,
               source, external_item_uuid, outlier_mark,
               weight_category, volume_category, price_group_id
        FROM prices
        WHERE loading_date >= %s AND loading_date <= %s
        ORDER BY id
    """
    with conn.cursor() as cur:
        cur.execute(query, (start_date, end_date))
        rows = cur.fetchall()
    logger.info("Извлечено %d строк из prices (%s — %s)",
                len(rows), start_date, end_date)
    return rows


def extract_exchange_rates(conn) -> dict:
    """Извлечение курсов ЦБ в виде lookup-словаря."""
    query = "SELECT num_code, value, setting_date FROM exchange_rates ORDER BY num_code, setting_date"
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(query)
        rates = {}
        for row in cur:
            key = (str(row["num_code"]), row["setting_date"].strftime("%Y-%m-%d"))
            rates[key] = float(row["value"])
    logger.info("Загружено %d курсов ЦБ", len(rates))
    return rates


def extract_body_types_mapping(conn) -> dict:
    """Извлечение маппинга body_type.id -> std_id."""
    query = "SELECT id, std_id FROM body_types WHERE std_id IS NOT NULL"
    with conn.cursor() as cur:
        cur.execute(query)
        return {row[0]: row[1] for row in cur.fetchall()}


def extract_price_body_types(conn) -> dict:
    """Извлечение price_id -> минимальный std_id через price_body_types."""
    query = """
        SELECT pbt.price_id, MIN(bt.std_id) as min_std_id
        FROM price_body_types pbt
        JOIN body_types bt ON bt.id = pbt.body_type_id
        WHERE bt.std_id IS NOT NULL
        GROUP BY pbt.price_id
    """
    with conn.cursor() as cur:
        cur.execute(query)
        return {row[0]: row[1] for row in cur.fetchall()}
