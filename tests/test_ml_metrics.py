"""
Тесты ML-метрик.

Проверяют, что обученная модель соответствует пороговым значениям:
  - R² > 0.8
  - MAPE < 20%
  - RMSE < 30% от среднего таргета
"""

import logging
import os
import pytest
import numpy as np

from ml.config import RANDOM_SEED
from ml.train import train_model
from ml.features import extract_features

logger = logging.getLogger(__name__)


# Пороговые значения метрик
MIN_R2 = 0.80
MAX_MAPE = 20.0
MAX_RMSE_PCT = 0.30  # RMSE не более 30% от среднего таргета


class TestMLMetrics:
    """Тестирование метрик ML-модели."""

    @pytest.fixture(scope="class")
    def trained_model(self, db_config):
        """Обученная модель CatBoost."""
        import psycopg2
        from etl.utils import get_connection

        with get_connection() as conn:
            df = extract_features(conn)
            if df.empty:
                pytest.skip("Нет данных для обучения")

            model, metrics = train_model(df)
            return model, metrics, df

    def test_r2_above_threshold(self, trained_model):
        """R² должен быть выше порога MIN_R2."""
        _, metrics, _ = trained_model
        r2 = metrics["R2"]
        logger.info("R² = %.4f (порог: %.2f)", r2, MIN_R2)
        assert r2 >= MIN_R2, f"R² {r2:.4f} ниже порога {MIN_R2}"

    def test_mape_below_threshold(self, trained_model):
        """MAPE должен быть ниже порога MAX_MAPE."""
        _, metrics, _ = trained_model
        mape = metrics["MAPE"]
        logger.info("MAPE = %.2f%% (порог: %.2f%%)", mape, MAX_MAPE)
        assert mape <= MAX_MAPE, f"MAPE {mape:.2f}% выше порога {MAX_MAPE}%"

    def test_rmse_reasonable(self, trained_model):
        """RMSE должен быть в разумных пределах."""
        model, metrics, df = trained_model
        rmse = metrics["RMSE"]
        mean_target = df["price_rub"].mean()
        rmse_pct = rmse / mean_target if mean_target > 0 else float("inf")
        logger.info("RMSE = %.2f (%.1f%% от среднего таргета %.2f)",
                     rmse, rmse_pct * 100, mean_target)
        assert rmse_pct <= MAX_RMSE_PCT, (
            f"RMSE {rmse:.2f} составляет {rmse_pct*100:.1f}% "
            f"от среднего таргета {mean_target:.2f}"
        )

    def test_model_reproducibility(self, db_config):
        """Модель должна быть воспроизводимой при одинаковом seed."""
        import psycopg2
        from etl.utils import get_connection

        with get_connection() as conn:
            df = extract_features(conn)
            if df.empty:
                pytest.skip("Нет данных для обучения")

            from ml.train import train_model

            _, metrics1, _ = train_model(df)
            _, metrics2, _ = train_model(df)

            # R² должен совпадать с точностью до 0.001
            assert abs(metrics1["R2"] - metrics2["R2"]) < 0.001, (
                "Модель не воспроизводима: R² различается"
            )

    def test_no_negative_predictions(self, trained_model):
        """Прогнозы не должны быть отрицательными."""
        model, _, df = trained_model
        from ml.scoring import batch_score

        df_scored = batch_score(model, df)
        assert (df_scored["predicted_price_rub"] >= 0).all(), (
            "Обнаружены отрицательные прогнозы"
        )
