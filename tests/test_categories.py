"""
Тесты категорий веса и объёма.

Проверяют:
  - weight_category по total_weight
  - volume_category по total_volume
"""

import pytest
import pandas as pd

from etl.core import enrich_categories


class TestWeightCategory:
    """Тестирование весовых категорий."""

    def test_g20t_heavy(self):
        """Вес >= 30т -> G20T."""
        df = pd.DataFrame({
            "id": [1],
            "total_weight": [35.0],
            "weight_category": [None],
        })
        result = enrich_categories(df)
        assert result.iloc[0]["weight_category"] == "G20T"

    def test_ftl_medium(self):
        """Вес [10; 30)т -> FTL."""
        df = pd.DataFrame({
            "id": [1],
            "total_weight": [15.0],
            "weight_category": [None],
        })
        result = enrich_categories(df)
        assert result.iloc[0]["weight_category"] == "FTL"

    def test_10t(self):
        """Вес [5; 10)т -> 10T."""
        df = pd.DataFrame({
            "id": [1],
            "total_weight": [7.5],
            "weight_category": [None],
        })
        result = enrich_categories(df)
        assert result.iloc[0]["weight_category"] == "10T"

    def test_5t(self):
        """Вес [2.5; 5)т -> 5T."""
        df = pd.DataFrame({
            "id": [1],
            "total_weight": [3.0],
            "weight_category": [None],
        })
        result = enrich_categories(df)
        assert result.iloc[0]["weight_category"] == "5T"

    def test_l5t_light(self):
        """Вес [0.5; 2.5)т -> L5T."""
        df = pd.DataFrame({
            "id": [1],
            "total_weight": [1.5],
            "weight_category": [None],
        })
        result = enrich_categories(df)
        assert result.iloc[0]["weight_category"] == "L5T"

    def test_oth_very_light(self):
        """Вес < 0.5т -> OTH."""
        df = pd.DataFrame({
            "id": [1],
            "total_weight": [0.3],
            "weight_category": [None],
        })
        result = enrich_categories(df)
        assert result.iloc[0]["weight_category"] == "OTH"

    def test_boundary_values(self):
        """Краевые значения категорий."""
        df = pd.DataFrame({
            "id": [1, 2, 3, 4, 5],
            "total_weight": [0.5, 2.5, 5.0, 10.0, 30.0],
            "weight_category": [None, None, None, None, None],
        })
        result = enrich_categories(df)
        assert result.iloc[0]["weight_category"] == "L5T"
        assert result.iloc[1]["weight_category"] == "5T"
        assert result.iloc[2]["weight_category"] == "10T"
        assert result.iloc[3]["weight_category"] == "FTL"
        assert result.iloc[4]["weight_category"] == "G20T"

    def test_existing_category_not_overwritten(self):
        """Существующая категория не должна перезаписываться."""
        df = pd.DataFrame({
            "id": [1],
            "total_weight": [15.0],
            "weight_category": ["FTL"],
        })
        result = enrich_categories(df)
        assert result.iloc[0]["weight_category"] == "FTL"


class TestVolumeCategory:
    """Тестирование объёмных категорий."""

    def test_ftl_volume(self):
        """Объём (0; 120] м³ -> FTL."""
        df = pd.DataFrame({
            "id": [1],
            "total_volume": [50.0],
            "volume_category": [None],
        })
        result = enrich_categories(df)
        assert result.iloc[0]["volume_category"] == "FTL"

    def test_oth_large_volume(self):
        """Объём > 120 м³ -> OTH."""
        df = pd.DataFrame({
            "id": [1],
            "total_volume": [150.0],
            "volume_category": [None],
        })
        result = enrich_categories(df)
        assert result.iloc[0]["volume_category"] == "OTH"

    def test_oth_zero_volume(self):
        """Объём = 0 -> OTH."""
        df = pd.DataFrame({
            "id": [1],
            "total_volume": [0.0],
            "volume_category": [None],
        })
        result = enrich_categories(df)
        assert result.iloc[0]["volume_category"] == "OTH"

    def test_existing_category_not_overwritten(self):
        """Существующая категория не должна перезаписываться."""
        df = pd.DataFrame({
            "id": [1],
            "total_volume": [50.0],
            "volume_category": ["FTL"],
        })
        result = enrich_categories(df)
        assert result.iloc[0]["volume_category"] == "FTL"
