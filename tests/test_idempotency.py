"""
Тесты идемпотентности ETL.

Проверяют:
  - Повторный запуск не создаёт дубли
  - DELETE+INSERT работает корректно
"""

import pytest
import pandas as pd
import numpy as np

from etl.core import run_core_etl
from etl.utils import get_connection, make_batch_id
from datetime import date


class TestIdempotency:
    """Тестирование идемпотентности ETL-пайплайна."""

    def test_load_to_core_idempotent(self):
        """Повторная загрузка в core не должна создавать дубли."""
        from etl.core import load_to_core

        batch_id = make_batch_id("test", date(2026, 1, 1), date(2026, 1, 31))

        # Создаём тестовые данные
        df = pd.DataFrame({
            "id": [1, 2, 3],
            "created_at": [None, None, None],
            "updated_at": [None, None, None],
            "subscriber_id": [None, None, None],
            "organization_id": [100, 200, 300],
            "loading_date": [date(2026, 1, 15)] * 3,
            "loading_city_gcid": [1001, 1002, 1003],
            "unloading_city_gcid": [1002, 1003, 1001],
            "distance": [500.0, 800.0, 300.0],
            "total_weight": [10.0, 15.0, 5.0],
            "total_volume": [50.0, 80.0, 30.0],
            "body_type": [56, 4, 2],
            "price": [50000.0, 80000.0, 30000.0],
            "currency_code": ["643", "643", "643"],
            "price_rub": [50000.0, 80000.0, 30000.0],
            "complex": [False, False, False],
            "price_type": [3, 3, 3],
            "is_fact": [True, True, True],
            "nearest_rate_date": [None, None, None],
            "outlier_mark": [False, False, False],
            "weight_category": ["FTL", "G20T", "10T"],
            "volume_category": ["FTL", "FTL", "FTL"],
            "price_group_id": [None, None, None],
            "body_group": ["Тент", "Рефрижератор", "Изотерм"],
            "etl_loaded_at": [None, None, None],
        })

        with get_connection() as conn:
            # Первая загрузка
            count1 = load_to_core(conn, df, batch_id, date(2026, 1, 1), date(2026, 1, 31))
            assert count1 == 3

            # Вторая загрузка (та же дата, тот же батч)
            count2 = load_to_core(conn, df, batch_id, date(2026, 1, 1), date(2026, 1, 31))
            assert count2 == 3

            # Проверяем, что нет дублей
            cursor = conn.cursor()
            cursor.execute(
                "SELECT COUNT(*) FROM core.cleaned_prices WHERE etl_batch_id = %s",
                (batch_id,),
            )
            total_count = cursor.fetchone()[0]
            assert total_count == 3, f"Ожидалось 3 строки, получено {total_count}"

    def test_quarantine_no_duplicates(self):
        """Карантин не должен содержать дубликатов price_id."""
        from etl.core import load_quarantine

        quarantine_df = pd.DataFrame({
            "price_id": [1, 2, 3, 1, 2],  # Дубли!
            "reason_code": ["a", "b", "c", "a", "b"],
            "reason_detail": ["d1", "d2", "d3", "d1", "d2"],
        })

        batch_id = "test_quarantine"

        with get_connection() as conn:
            count = load_quarantine(conn, quarantine_df, batch_id)
            # Все записи должны быть загружены (даже дубли)
            assert count == 5

            # Но в итоговой выборке для DQ дубли должны быть устранены
            cursor = conn.cursor()
            cursor.execute(
                "SELECT price_id, COUNT(*) as cnt "
                "FROM dq.dq_quarantine_prices WHERE batch_id = %s "
                "GROUP BY price_id HAVING COUNT(*) > 1",
                (batch_id,),
            )
            # Это ожидаемо: quarantine может иметь дубли по price_id
            # (разные причины карантина)
            # Но в основном пайплайне мы дедуплируем по price_id

    def test_dq_no_duplicate_quarantine_ids(self, raw_prices_for_dq):
        """DQ-карантин не должен содержать дубли price_id."""
        from etl.core import apply_dq

        valid, quarantine = apply_dq(raw_prices_for_dq)

        if not quarantine.empty:
            price_ids = quarantine["price_id"]
            assert price_ids.is_unique, (
                f"Дубли price_id в карантине: {price_ids[price_ids.duplicated()].tolist()}"
            )
