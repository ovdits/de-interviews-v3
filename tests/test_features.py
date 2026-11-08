"""
Тесты Feature Engineering для ML-модели.

Проверяют:
  - Извлечение регионов из full_name_local
  - Циклическое кодирование дат (sin/cos)
  - Фильтрация leakage-признаков
"""

import pytest
import pandas as pd
import numpy as np

from ml.features import engineer_features


class TestRegionExtraction:
    """Тестирование извлечения регионов."""

    def test_region_from_full_name(self):
        """Регион извлекается из full_name_local (текст после запятой)."""
        df = pd.DataFrame({
            "id": [1, 2, 3],
            "loading_date": [pd.Timestamp("2026-09-15")] * 3,
            "loading_city_gcid": [1001, 1002, 1003],
            "unloading_city_gcid": [1002, 1003, 1001],
            "distance": [500.0, 800.0, 300.0],
            "total_weight": [10.0, 15.0, 5.0],
            "total_volume": [50.0, 80.0, 30.0],
            "body_group": ["Тент", "Рефрижератор", "Изотерм"],
            "weight_category": ["FTL", "G20T", "10T"],
            "volume_category": ["FTL", "FTL", "FTL"],
            "price_rub": [50000.0, 80000.0, 30000.0],
            "origin_full_name": ["Москва, г. Москва", "Казань, Республика Татарстан", "Екатеринбург, Свердловская обл."],
            "dest_full_name": ["Казань, Республика Татарстан", "Екатеринбург, Свердловская обл.", "Москва, г. Москва"],
            "origin_lat": [55.7558, 55.7961, 56.8389],
            "origin_lon": [37.6173, 49.1064, 60.6057],
            "dest_lat": [55.7961, 56.8389, 55.7558],
            "dest_lon": [49.1064, 60.6057, 37.6173],
        })

        result = engineer_features(df)

        # Проверяем, что регионы извлечены
        assert "origin_region" in result.columns
        assert "destination_region" in result.columns

        # Проверяем значения
        assert result.iloc[0]["origin_region"] == "г. Москва"
        assert result.iloc[0]["destination_region"] == "Республика Татарстан"
        assert result.iloc[1]["origin_region"] == "Республика Татарстан"
        assert result.iloc[2]["origin_region"] == "Свердловская обл."

    def test_missing_region_fallback(self):
        """При отсутствии региона используется 'Не указан'."""
        df = pd.DataFrame({
            "id": [1],
            "loading_date": [pd.Timestamp("2026-09-15")],
            "loading_city_gcid": [1001],
            "unloading_city_gcid": [1002],
            "distance": [500.0],
            "total_weight": [10.0],
            "total_volume": [50.0],
            "body_group": ["Тент"],
            "weight_category": ["FTL"],
            "volume_category": ["FTL"],
            "price_rub": [50000.0],
            "origin_full_name": [None],
            "dest_full_name": [None],
            "origin_lat": [55.7558],
            "origin_lon": [37.6173],
            "dest_lat": [55.7961],
            "dest_lon": [49.1064],
        })

        result = engineer_features(df)

        assert result.iloc[0]["origin_region"] == "Не указан"
        assert result.iloc[0]["destination_region"] == "Не указан"


class TestCyclicalEncoding:
    """Тестирование циклического кодирования дат."""

    def test_sin_cos_month(self):
        """Месяц кодируется через sin/cos (T=12)."""
        df = pd.DataFrame({
            "id": [1, 2, 3],
            "loading_date": [
                pd.Timestamp("2026-01-15"),  # Январь
                pd.Timestamp("2026-06-15"),  # Июнь
                pd.Timestamp("2026-12-15"),  # Декабрь
            ],
            "loading_city_gcid": [1001] * 3,
            "unloading_city_gcid": [1002] * 3,
            "distance": [500.0] * 3,
            "total_weight": [10.0] * 3,
            "total_volume": [50.0] * 3,
            "body_group": ["Тент"] * 3,
            "weight_category": ["FTL"] * 3,
            "volume_category": ["FTL"] * 3,
            "price_rub": [50000.0] * 3,
            "origin_full_name": ["Москва, г. Москва"] * 3,
            "dest_full_name": ["Казань, Республика Татарстан"] * 3,
            "origin_lat": [55.7558] * 3,
            "origin_lon": [37.6173] * 3,
            "dest_lat": [55.7961] * 3,
            "dest_lon": [49.1064] * 3,
        })

        result = engineer_features(df)

        assert "sin_month" in result.columns
        assert "cos_month" in result.columns

        # Январь (месяц 1): sin(2π*1/12) ≈ 0.517, cos(2π*1/12) ≈ 0.856
        jan_sin = np.sin(2 * np.pi * 1 / 12)
        jan_cos = np.cos(2 * np.pi * 1 / 12)
        assert abs(result.iloc[0]["sin_month"] - jan_sin) < 0.001
        assert abs(result.iloc[0]["cos_month"] - jan_cos) < 0.001

        # Июнь (месяц 6): sin(2π*6/12) = 0, cos(2π*6/12) = -1
        assert abs(result.iloc[1]["sin_month"]) < 0.001
        assert abs(result.iloc[1]["cos_month"] - (-1.0)) < 0.001

    def test_sin_cos_dow(self):
        """День недели кодируется через sin/cos (T=7)."""
        df = pd.DataFrame({
            "id": [1],
            "loading_date": [pd.Timestamp("2026-09-15")],  # Вторник (dow=1)
            "loading_city_gcid": [1001],
            "unloading_city_gcid": [1002],
            "distance": [500.0],
            "total_weight": [10.0],
            "total_volume": [50.0],
            "body_group": ["Тент"],
            "weight_category": ["FTL"],
            "volume_category": ["FTL"],
            "price_rub": [50000.0],
            "origin_full_name": ["Москва, г. Москва"],
            "dest_full_name": ["Казань, Республика Татарстан"],
            "origin_lat": [55.7558],
            "origin_lon": [37.6173],
            "dest_lat": [55.7961],
            "dest_lon": [49.1064],
        })

        result = engineer_features(df)

        assert "sin_dow" in result.columns
        assert "cos_dow" in result.columns

        # Вторник (dow=1): sin(2π*1/7), cos(2π*1/7)
        expected_sin = np.sin(2 * np.pi * 1 / 7)
        expected_cos = np.cos(2 * np.pi * 1 / 7)
        assert abs(result.iloc[0]["sin_dow"] - expected_sin) < 0.001
        assert abs(result.iloc[0]["cos_dow"] - expected_cos) < 0.001

    def test_sin_cos_doy(self):
        """День года кодируется через sin/cos (T=365.25)."""
        df = pd.DataFrame({
            "id": [1],
            "loading_date": [pd.Timestamp("2026-01-01")],  # День 1
            "loading_city_gcid": [1001],
            "unloading_city_gcid": [1002],
            "distance": [500.0],
            "total_weight": [10.0],
            "total_volume": [50.0],
            "body_group": ["Тент"],
            "weight_category": ["FTL"],
            "volume_category": ["FTL"],
            "price_rub": [50000.0],
            "origin_full_name": ["Москва, г. Москва"],
            "dest_full_name": ["Казань, Республика Татарстан"],
            "origin_lat": [55.7558],
            "origin_lon": [37.6173],
            "dest_lat": [55.7961],
            "dest_lon": [49.1064],
        })

        result = engineer_features(df)

        assert "sin_doy" in result.columns
        assert "cos_doy" in result.columns

        # День 1: sin(2π*1/365.25), cos(2π*1/365.25)
        expected_sin = np.sin(2 * np.pi * 1 / 365.25)
        expected_cos = np.cos(2 * np.pi * 1 / 365.25)
        assert abs(result.iloc[0]["sin_doy"] - expected_sin) < 0.001
        assert abs(result.iloc[0]["cos_doy"] - expected_cos) < 0.001


class TestLeakageFiltering:
    """Тестирование фильтрации leakage-признаков."""

    def test_leakage_features_removed(self):
        """Leakage-признаки должны быть удалены."""
        df = pd.DataFrame({
            "id": [1],
            "loading_date": [pd.Timestamp("2026-09-15")],
            "loading_city_gcid": [1001],
            "unloading_city_gcid": [1002],
            "distance": [500.0],
            "total_weight": [10.0],
            "total_volume": [50.0],
            "body_group": ["Тент"],
            "weight_category": ["FTL"],
            "volume_category": ["FTL"],
            "price_rub": [50000.0],
            "origin_full_name": ["Москва, г. Москва"],
            "dest_full_name": ["Казань, Республика Татарстан"],
            "origin_lat": [55.7558],
            "origin_lon": [37.6173],
            "dest_lat": [55.7961],
            "dest_lon": [49.1064],
            "price_group_id": [123],  # Leakage!
            "avg_price": [55000.0],   # Leakage!
        })

        result = engineer_features(df)

        # Leakage-признаки должны быть удалены
        assert "price_group_id" not in result.columns
        assert "avg_price" not in result.columns
        assert "price_with_vat" not in result.columns
