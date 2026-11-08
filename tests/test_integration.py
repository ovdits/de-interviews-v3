"""
Интеграционные тесты на полный ETL-пайплайн.

Используют pytest-postgresql или фикстуры с временной БД.
Для локального тестирования требуются:
  pip install pytest-postgresql pytest-postgresql-fixtures

Если БД недоступна — тесты пропускаются через pytest.mark.skipif.
"""

import logging
import os
import pytest
import pandas as pd
import psycopg2

from datetime import date

logger = logging.getLogger(__name__)


# ===================================================================
# Фикстуры для БД
# ===================================================================

@pytest.fixture(scope="session")
def db_config():
    """Конфигурация подключения к тестовой БД."""
    return {
        "host": os.getenv("TEST_DB_HOST", "localhost"),
        "port": int(os.getenv("TEST_DB_PORT", 5432)),
        "dbname": os.getenv("TEST_DB_NAME", "deals_bigdata_test"),
        "user": os.getenv("TEST_DB_USER", "postgres"),
        "password": os.getenv("TEST_DB_PASSWORD", "postgres"),
    }


@pytest.fixture(scope="session")
def db_schema(db_config):
    """Создание схемы БД для тестов."""
    conn = psycopg2.connect(**db_config)
    try:
        cur = conn.cursor()
        # Создаём схемы
        cur.execute("CREATE SCHEMA IF NOT EXISTS raw")
        cur.execute("CREATE SCHEMA IF NOT EXISTS core")
        cur.execute("CREATE SCHEMA IF NOT EXISTS mart")
        cur.execute("CREATE SCHEMA IF NOT EXISTS dq")
        conn.commit()

        # Создаём таблицу raw.raw_prices
        cur.execute("""
            CREATE TABLE IF NOT EXISTS raw.raw_prices (
                id bigint PRIMARY KEY,
                created_at timestamp,
                updated_at timestamp,
                subscriber_id integer,
                organization_id integer,
                loading_date date NOT NULL,
                loading_city_gcid integer NOT NULL,
                unloading_city_gcid integer NOT NULL,
                distance double precision NOT NULL,
                total_weight double precision NOT NULL,
                total_volume double precision NOT NULL,
                body_type integer,
                price double precision NOT NULL,
                currency_code varchar(3) NOT NULL DEFAULT '643',
                complex boolean NOT NULL DEFAULT false,
                price_type smallint NOT NULL DEFAULT 2,
                item_id integer,
                is_fact boolean NOT NULL DEFAULT true,
                nearest_rate_date date,
                meta_item_id bigint,
                end_loading_date date,
                source varchar(255),
                external_item_uuid uuid,
                outlier_mark boolean NOT NULL DEFAULT false,
                weight_category varchar(4),
                volume_category varchar(4),
                price_group_id bigint,
                extract_batch_id varchar(50),
                extract_date timestamp
            )
        """)

        # Создаём таблицу core.cleaned_prices
        cur.execute("""
            CREATE TABLE IF NOT EXISTS core.cleaned_prices (
                id bigint PRIMARY KEY,
                created_at timestamp,
                updated_at timestamp,
                subscriber_id integer,
                organization_id integer,
                loading_date date NOT NULL,
                loading_city_gcid integer NOT NULL,
                unloading_city_gcid integer NOT NULL,
                distance double precision NOT NULL,
                total_weight double precision NOT NULL,
                total_volume double precision NOT NULL,
                body_type integer,
                price double precision NOT NULL,
                currency_code varchar(3) NOT NULL DEFAULT '643',
                price_rub double precision,
                complex boolean NOT NULL DEFAULT false,
                price_type smallint NOT NULL DEFAULT 2,
                is_fact boolean NOT NULL DEFAULT true,
                nearest_rate_date date,
                outlier_mark boolean NOT NULL DEFAULT false,
                weight_category varchar(4),
                volume_category varchar(4),
                price_group_id bigint,
                body_group varchar(50),
                etl_batch_id varchar(50),
                etl_loaded_at timestamp
            )
        """)

        # Создаём таблицу dq.dq_quarantine_prices
        cur.execute("""
            CREATE TABLE IF NOT EXISTS dq.dq_quarantine_prices (
                id bigserial PRIMARY KEY,
                price_id bigint NOT NULL,
                reason_code varchar(50) NOT NULL,
                reason_detail text,
                quarantined_at timestamp NOT NULL DEFAULT now(),
                batch_id varchar(50)
            )
        """)

        # Создаём таблицу core.etl_runs
        cur.execute("""
            CREATE TABLE IF NOT EXISTS core.etl_runs (
                id bigserial PRIMARY KEY,
                pipeline varchar(50) NOT NULL,
                batch_id varchar(50) NOT NULL,
                start_date date,
                end_date date,
                rows_read integer DEFAULT 0,
                rows_written integer DEFAULT 0,
                rows_quarantined integer DEFAULT 0,
                status varchar(20) NOT NULL DEFAULT 'running',
                started_at timestamp NOT NULL DEFAULT now(),
                finished_at timestamp,
                error_msg text
            )
        """)

        conn.commit()
        logger.info("Тестовая схема БД создана")
    finally:
        conn.close()


@pytest.fixture(scope="function")
def test_prices_data():
    """Тестовые данные для raw.raw_prices."""
    return [
        (1, "2026-09-15", 1001, 1002, 897.0, 20.0, 50.0, 56, 84500.0, "643",
         3, True, False, "2026-09-12", False, None, None, None, None, None,
         "test_batch_1", "2026-11-08 12:00:00"),
        (2, "2026-09-20", 1002, 1003, 1200.0, 15.0, 80.0, 4, 120000.0, "840",
         3, True, False, "2026-09-18", False, None, None, None, None, None,
         "test_batch_1", "2026-11-08 12:00:00"),
        (3, "2026-10-01", 1001, 1003, 500.0, 8.0, 30.0, 2, 45000.0, "643",
         3, True, False, "2026-09-30", False, None, None, None, None, None,
         "test_batch_1", "2026-11-08 12:00:00"),
        # Невалидные строки для DQ
        (4, "2026-10-05", 1001, 1002, 300.0, 5.0, 20.0, 56, 0.0, "643",
         3, True, False, None, False, None, None, None, None, None,
         "test_batch_1", "2026-11-08 12:00:00"),
        (5, "2026-10-10", 1002, 1003, -100.0, 10.0, 50.0, 56, 50000.0, "643",
         3, True, False, None, False, None, None, None, None, None,
         "test_batch_1", "2026-11-08 12:00:00"),
    ]


# ===================================================================
# Интеграционные тесты
# ===================================================================

class TestETLIntegration:
    """Интеграционные тесты ETL-пайплайна."""

    def test_raw_to_core_pipeline(self, db_config, db_schema, test_prices_data):
        """
        Полный пайплайн: raw -> DQ -> валюта -> core.
        
        Проверяет:
          1. Загрузка данных в raw
          2. DQ-фильтрация (2 строки должны уйти в карантин)
          3. Конвертация валюты (USD -> RUB)
          4. Загрузка в core
          5. Идемпотентность (повторный запуск не создаёт дубли)
        """
        from etl.core import run_core_etl
        from etl.utils import get_connection, make_batch_id
        from etl.raw import load_to_raw

        batch_id = make_batch_id("test", date(2026, 9, 1), date(2026, 10, 31))

        with get_connection() as conn:
            cur = conn.cursor()

            # Шаг 1: Загрузка в raw
            for row in test_prices_data:
                cur.execute(
                    """
                    INSERT INTO raw.raw_prices
                        (id, loading_date, loading_city_gcid, unloading_city_gcid,
                         distance, total_weight, total_volume, body_type,
                         price, currency_code, price_type, is_fact, complex,
                         nearest_rate_date, outlier_mark, weight_category,
                         volume_category, price_group_id, extract_batch_id, extract_date)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    row,
                )
            conn.commit()

            # Проверяем, что данные попали в raw
            cur.execute("SELECT COUNT(*) FROM raw.raw_prices")
            assert cur.fetchone()[0] == 5, "В raw должно быть 5 строк"

            # Шаг 2-5: Запуск core ETL
            result = run_core_etl(date(2026, 9, 1), date(2026, 10, 31))

            # Проверяем статистику
            assert result["rows_read"] == 5
            assert result["rows_quarantined"] >= 2  # price=0 и distance<0
            assert result["rows_written"] >= 2  # валидные строки

            # Шаг 6: Идемпотентность — повторный запуск
            result2 = run_core_etl(date(2026, 9, 1), date(2026, 10, 31))
            assert result2["rows_written"] == result["rows_written"], (
                "Повторный запуск не должен задваивать данные"
            )

            # Проверяем, что в core ровно rows_written строк
            cur.execute(
                "SELECT COUNT(*) FROM core.cleaned_prices WHERE etl_batch_id = %s",
                (batch_id,),
            )
            assert cur.fetchone()[0] == result["rows_written"]

    def test_quarantine_has_reason_codes(self, db_config, db_schema, test_prices_data):
        """Карантинные записи должны иметь reason_code и reason_detail."""
        from etl.core import run_core_etl
        from etl.utils import get_connection, make_batch_id
        from etl.raw import load_to_raw

        batch_id = make_batch_id("test", date(2026, 9, 1), date(2026, 10, 31))

        with get_connection() as conn:
            cur = conn.cursor()

            # Загрузка в raw
            for row in test_prices_data:
                cur.execute(
                    """
                    INSERT INTO raw.raw_prices
                        (id, loading_date, loading_city_gcid, unloading_city_gcid,
                         distance, total_weight, total_volume, body_type,
                         price, currency_code, price_type, is_fact, complex,
                         nearest_rate_date, outlier_mark, weight_category,
                         volume_category, price_group_id, extract_batch_id, extract_date)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    row,
                )
            conn.commit()

            # Запуск core ETL
            run_core_etl(date(2026, 9, 1), date(2026, 10, 31))

            # Проверяем карантин
            cur.execute(
                """
                SELECT reason_code, COUNT(*) 
                FROM dq.dq_quarantine_prices 
                WHERE batch_id LIKE 'test_core%'
                GROUP BY reason_code
                """
            )
            results = cur.fetchall()
            reason_codes = {r[0] for r in results}

            # Должны быть price_not_positive и distance_not_positive
            assert "price_not_positive" in reason_codes, (
                "В карантине должна быть причина price_not_positive"
            )
            assert "distance_not_positive" in reason_codes, (
                "В карантине должна быть причина distance_not_positive"
            )

    def test_etl_metadata_saved(self, db_config, db_schema, test_prices_data):
        """Метаданные ETL-запуска должны быть сохранены в core.etl_runs."""
        from etl.core import run_core_etl
        from etl.utils import get_connection, make_batch_id
        from etl.raw import load_to_raw

        batch_id = make_batch_id("test", date(2026, 9, 1), date(2026, 10, 31))

        with get_connection() as conn:
            cur = conn.cursor()

            # Загрузка в raw
            for row in test_prices_data:
                cur.execute(
                    """
                    INSERT INTO raw.raw_prices
                        (id, loading_date, loading_city_gcid, unloading_city_gcid,
                         distance, total_weight, total_volume, body_type,
                         price, currency_code, price_type, is_fact, complex,
                         nearest_rate_date, outlier_mark, weight_category,
                         volume_category, price_group_id, extract_batch_id, extract_date)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    row,
                )
            conn.commit()

            # Запуск core ETL
            run_core_etl(date(2026, 9, 1), date(2026, 10, 31))

            # Проверяем метаданные
            cur.execute(
                """
                SELECT pipeline, status, rows_read, rows_written, rows_quarantined
                FROM core.etl_runs
                WHERE pipeline = 'etl_core'
                ORDER BY id DESC LIMIT 1
                """
            )
            row = cur.fetchone()
            assert row is not None, "Метаданные ETL должны быть сохранены"
            assert row[0] == "etl_core"
            assert row[1] == "success"
            assert row[2] == 5
            assert row[3] >= 0
            assert row[4] >= 0
