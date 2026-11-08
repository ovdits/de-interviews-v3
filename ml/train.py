"""
Обучение ML-модели прогнозирования тарифов.

Используем CatBoostRegressor с Time-Based Split.
Метрики: R², MAPE, RMSE, MAE.
"""

import logging
from datetime import date

import numpy as np
import pandas as pd
from catboost import CatBoostRegressor
from sklearn.metrics import (
    r2_score, mean_absolute_error, mean_squared_error,
)

from ml.config import (
    RANDOM_SEED, CATBOOST_PARAMS, CAT_FEATURES,
    NUM_FEATURES, TARGET,
)
from ml.features import (
    extract_features, engineer_features, split_time_series, prepare_X_y,
)
from etl.utils import get_connection

logger = logging.getLogger(__name__)


def train_model(df: pd.DataFrame) -> tuple:
    """
    Обучение модели CatBoostRegressor.

    Этапы:
      1. Feature Engineering
      2. Time-Based Split (train/test)
      3. Подготовка признаков
      4. Обучение CatBoost
      5. Оценка на test-выборке

    Args:
        df: DataFrame с сырыми данными

    Returns:
        (model, metrics_dict)
    """
    logger.info("=" * 60)
    logger.info("Начало обучения модели")
    logger.info("=" * 60)

    # Feature Engineering
    logger.info("Этап 1: Feature Engineering")
    df_features = engineer_features(df)

    # Time-Based Split
    logger.info("Этап 2: Time-Based Split")
    df_train, df_test = split_time_series(df_features)

    if len(df_train) == 0 or len(df_test) == 0:
        raise ValueError(
            f"Пустая выборка: train={len(df_train)}, test={len(df_test)}. "
            f"Данные должны покрывать [{df_train['loading_date'].min()}, {df_test['loading_date'].max()}]"
        )

    # Подготовка признаков (city frequency считаем на train)
    logger.info("Этап 3: Подготовка признаков")
    X_train, y_train = prepare_X_y(df_train)
    X_test, y_test = prepare_X_y(df_test)

    # Определяем категориальные колонки
    cat_cols = [c for c in CAT_FEATURES if c in X_train.columns]
    logger.info("Категориальные признаки: %s", cat_cols)

    # Обучение модели
    logger.info("Этап 4: Обучение CatBoostRegressor")
    model = CatBoostRegressor(**CATBOOST_PARAMS)
    model.fit(
        X_train, y_train,
        cat_features=cat_cols,
        eval_set=(X_test, y_test),
    )

    # Оценка на test-выборке
    logger.info("Этап 5: Оценка на test-выборке")
    y_pred = model.predict(X_test)

    # Запрещаем отрицательные прогнозы
    y_pred = np.maximum(y_pred, 0)

    # Расчёт метрик
    r2 = r2_score(y_test, y_pred)
    mae = mean_absolute_error(y_test, y_pred)
    rmse = np.sqrt(mean_squared_error(y_test, y_pred))
    mape = np.mean(np.abs((y_test - y_pred) / y_test)) * 100

    metrics = {
        "R2": round(r2, 4),
        "MAE": round(mae, 2),
        "RMSE": round(rmse, 2),
        "MAPE": round(mape, 2),
        "train_rows": len(df_train),
        "test_rows": len(df_test),
    }

    logger.info("=" * 60)
    logger.info("Метрики на test-выборке:")
    logger.info("  R²:   %.4f", metrics["R2"])
    logger.info("  MAE:  %.2f руб.", metrics["MAE"])
    logger.info("  RMSE: %.2f руб.", metrics["RMSE"])
    logger.info("  MAPE: %.2f %%", metrics["MAPE"])
    logger.info("  train: %d строк, test: %d строк", metrics["train_rows"], metrics["test_rows"])
    logger.info("=" * 60)

    # Важность признаков
    importances = model.feature_importances_
    feat_imp = pd.Series(importances, index=X_train.columns).sort_values(ascending=False)
    logger.info("Важность признаков (топ-5):")
    for feat, imp in feat_imp.head(5).items():
        logger.info("  %s: %.2f", feat, imp)

    return model, metrics


def save_model(model: CatBoostRegressor, path: str = None) -> str:
    """
    Сохранение обученной модели на диск.

    Args:
        model: обученная CatBoostRegressor
        path: путь для сохранения (по умолчанию ml/model.cbm)

    Returns:
        Путь к сохранённой модели
    """
    if path is None:
        import os
        path = os.path.join(os.path.dirname(__file__), "model.cbm")

    model.save_model(path)
    logger.info("Модель сохранена: %s", path)
    return path


def run_training(conn) -> dict:
    """
    Полный цикл обучения: извлечение -> feature engineering -> обучение.

    Args:
        conn: соединение с БД

    Returns:
        Словарь с метриками
    """
    with get_connection() as conn:
        # Извлечение данных
        logger.info("Извлечение данных для обучения...")
        df = extract_features(conn)

        if df.empty:
            raise ValueError("Нет данных для обучения")

        # Обучение
        model, metrics = train_model(df)

        # Сохранение модели
        save_model(model)

    return metrics
