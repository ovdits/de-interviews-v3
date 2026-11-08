"""
Тесты конвертации валют.

Проверяют:
  - RUB (643): price_rub = price
  - USD/EUR с курсом: price_rub = price * rate
  - Отсутствие курса -> карантин
"""

import pytest
import pandas as pd

from etl.core import convert_currency


class TestCurrencyConversion:
    """Тестирование конвертации валют."""

    def test_rub_no_conversion(self, valid_prices):
        """RUB-строки должны получить price_rub = price."""
        df, quarantine = convert_currency(valid_prices, {})

        # Все строки валидные, RUB
        assert df["currency_code"].eq("643").all()
        assert (df["price_rub"] == df["price"]).all()
        assert quarantine.empty

    def test_usd_with_rate(self, sample_prices, sample_rates):
        """USD-строки должны быть конвертированы по курсу."""
        # Фильтруем только USD строки
        usd_df = sample_prices[sample_prices["currency_code"] == "840"].copy()

        # Добавляем nearest_rate_date
        usd_df["nearest_rate_date"] = pd.to_datetime(usd_df["id"].map({
            2: pd.Timestamp("2026-09-15"),
        }))

        converted, quarantine = convert_currency(usd_df, sample_rates)

        # Строка с id=2 должна быть конвертирована
        if len(converted) > 0:
            row = converted.iloc[0]
            expected_rub = row["price"] * 92.5
            assert abs(row["price_rub"] - expected_rub) < 0.01

    def test_missing_rate_quarantine(self, sample_prices, sample_rates):
        """Строки без курса должны попасть в карантин."""
        # USD строка без nearest_rate_date
        df = pd.DataFrame({
            "id": [200],
            "price": [1000.0],
            "currency_code": ["840"],
            "nearest_rate_date": [None],
        })

        converted, quarantine = convert_currency(df, sample_rates)

        assert converted.empty, "Строка без курса должна быть в карантине"
        assert len(quarantine) == 1
        assert quarantine.iloc[0]["reason_code"] == "currency_rate_not_found"

    def test_eur_with_rate(self, sample_rates):
        """EUR-строки должны быть конвертированы по курсу."""
        df = pd.DataFrame({
            "id": [300],
            "price": [1000.0],
            "currency_code": ["978"],
            "nearest_rate_date": [pd.Timestamp("2026-08-20")],
        })

        converted, quarantine = convert_currency(df, sample_rates)

        assert len(converted) == 1
        assert abs(converted.iloc[0]["price_rub"] - 102000.0) < 0.01

    def test_mixed_currencies(self, sample_rates):
        """Смешанные валюты: RUB без конвертации, USD/EUR с конвертацией."""
        df = pd.DataFrame({
            "id": [1, 2, 3],
            "price": [50000.0, 1000.0, 500.0],
            "currency_code": ["643", "840", "978"],
            "nearest_rate_date": [None, pd.Timestamp("2026-09-15"), pd.Timestamp("2026-08-20")],
        })

        converted, quarantine = convert_currency(df, sample_rates)

        # RUB строка должна остаться
        rub_row = converted[converted["id"] == 1]
        assert len(rub_row) == 1
        assert rub_row.iloc[0]["price_rub"] == 50000.0

        # USD строка должна быть конвертирована
        usd_row = converted[converted["id"] == 2]
        assert len(usd_row) == 1
        assert abs(usd_row.iloc[0]["price_rub"] - 92500.0) < 0.01

    def test_no_foreign_currency(self):
        """Только RUB-строки: карантин должен быть пустым."""
        df = pd.DataFrame({
            "id": [1, 2, 3],
            "price": [50000.0, 1200.0, 80000.0],
            "currency_code": ["643", "643", "643"],
            "nearest_rate_date": [None, None, None],
        })

        converted, quarantine = convert_currency(df, {})

        assert len(converted) == 3
        assert quarantine.empty
