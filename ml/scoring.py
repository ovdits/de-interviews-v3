"""
Batch Inference: пакетный скоринг ML-модели.

Прогнозирует тарифы для test-периода и будущих заявок.
Результаты записываются в mart.ml_price_predictions.
"""

import logging
import os
from datetime import date

import numpy as np
import pandas as pd
from catboost import CatBoostRegressor

from ml.config import (
    AS_OF_DATE, TEST_START, TEST_END, MODEL_NAME,
    CAT_FEATURES, NUM_FEATURES, TARGET,
)
from ml.features import (
    extract_features_for_scoring, engineer_features, prepare_X_y,
)
from etl.utils import get_connection
from psycopg2.extras import execute_values

logger = logging.getLogger(__name__)


def load_model(path: str = None) -> CatBoostRegressor:
    """
    Загрузка обученной модели.

    Args:
        path: путь к модели (по умолчанию ml/model.cbm)

    Returns:
        Загруженная модель
    """
    if path is None:
        path = os.path.join(os.path.dirname(__file__), "model.cbm")

    if not os.path.exists(path):
        raise FileNotFoundError(f"Модель не найдена: {path}. Сначала запустите обучение.")

    model = CatBoostRegressor()
    model.load_model(path)
    logger.info("Модель загружена: %s", path)
    return model


def batch_score(model: CatBoostRegressor, df: pd.DataFrame) -> pd.DataFrame:
    """
    Пакетный скоринг: предсказание тарифов для всех строк.

    Args:
        model: обученная CatBoostRegressor
        df: DataFrame с инженерными признаками

    Returns:
        DataFrame с прогнозами
    """
    X, _ = prepare_X_y(df)

    # Определяем категориальные колонки
    cat_cols = [c for c in CAT_FEATURES if c in X.columns]

    # Прогноз
    y_pred = model.predict(X)
    y_pred = np.maximum(y_pred, 0)  # Запрещаем отрицательные прогнозы

    df = df.copy()
    df["predicted_price_rub"] = y_pred

    logger.info("Выполнен batch-скоринг: %d строк", len(df))
    return df


def compute_errors(df: pd.DataFrame) -> pd.DataFrame:
    """
    Расчёт абсолютной и относительной ошибки прогноза.

    Для будущих заявок (loading_date > AS_OF_DATE) actual_price_rub = NULL.
    """
    df = df.copy()

    # Разделяем на фактические и будущие заявки
    is_future = df["loading_date"] > AS_OF_DATE

    # Для будущих заявок actual = NULL
    df.loc[is_future, "actual_price_rub"] = None

    # Расчёт ошибок только для фактических заявок
    mask_actual = df["actual_price_rub"].notna()
    df.loc[mask_actual, "abs_error_rub"] = np.abs(
        df.loc[mask_actual, "actual_price_rub"] - df.loc[mask_actual, "predicted_price_rub"]
    )
    df.loc[mask_actual, "ape_pct"] = (
        df.loc[mask_actual, "abs_error_rub"] / df.loc[mask_actual, "actual_price_rub"] * 100
    )

    return df


def load_predictions_to_db(conn, predictions_df: pd.DataFrame,
                           model_name: str = MODEL_NAME) -> int:
    """
    Загрузка прогнозов в mart.ml_price_predictions.

    Идемпотентно: удаляем старые прогнозы за тот же период.
    """
    cursor = conn.cursor()

    # Удаляем старые прогнозы
    cursor.execute(
        """
        DELETE FROM mart.ml_price_predictions
        WHERE loading_date >= %s AND loading_date <= %s
        """,
        (TEST_START, TEST_END),
    )

    if predictions_df.empty:
        logger.info("Нет прогнозов для загрузки")
        return 0

    # Формируем данные для вставки (векторизованно через to_numpy)
    columns_to_insert = [
        "price_id", "loading_date", "loading_city_gcid",
        "unloading_city_gcid", "body_group", "actual_price_rub",
        "predicted_price_rub", "abs_error_rub", "ape_pct",
    ]
    rows = predictions_df[columns_to_insert].values.tolist()
    # Добавляем model_name в каждую строку
    rows = [r + [model_name] for r in rows]

    execute_values(
        cursor,
        """
        INSERT INTO mart.ml_price_predictions
            (price_id, loading_date, loading_city_gcid, unloading_city_gcid,
             body_group, actual_price_rub, predicted_price_rub,
             abs_error_rub, ape_pct, model_name)
        VALUES %s
        """,
        rows,
        page_size=10000,
    )

    logger.info("Загружено %d прогнозов в mart.ml_price_predictions", len(rows))
    return len(rows)


def run_scoring(conn, model: CatBoostRegressor = None) -> dict:
    """
    Полный цикл скоринга: извлечение -> прогноз -> ошибки -> запись в БД.

    Args:
        conn: соединение с БД
        model: загруженная модель (если None, загрузится из файла)

    Returns:
        Словарь со статистикой скоринга
    """
    if model is None:
        model = load_model()

    logger.info("=" * 60)
    logger.info("Начало batch-скоринга")
    logger.info("=" * 60)

    with get_connection() as conn:
        # Извлечение данных для скоринга
        logger.info("Извлечение данных для скоринга...")
        df_raw = extract_features_for_scoring(conn)

        if df_raw.empty:
            raise ValueError("Нет данных для скоринга")

        # Feature Engineering
        logger.info("Feature Engineering...")
        df_features = engineer_features(df_raw)

        # Batch Scoring
        logger.info("Batch Scoring...")
        df_scored = batch_score(model, df_features)

        # Расчёт ошибок
        logger.info("Расчёт ошибок...")
        df_scored = compute_errors(df_scored)

        # Статистика
        actual_mask = df_scored["actual_price_rub"].notna()
        future_mask = df_scored["loading_date"] > AS_OF_DATE

        logger.info("Статистика скоринга:")
        logger.info("  Всего строк:        %d", len(df_scored))
        logger.info("  Фактические заявки:  %d", actual_mask.sum())
        logger.info("  Будущие заявки:      %d", future_mask.sum())

        if actual_mask.any():
            actual_df = df_scored[actual_mask]
            mape_actual = actual_df["ape_pct"].mean()
            mae_actual = actual_df["abs_error_rub"].mean()
            logger.info("  MAPE (факт):         %.2f %%", mape_actual)
            logger.info("  MAE (факт):          %.2f руб.", mae_actual)

        # Загрузка в БД
        logger.info("Загрузка прогнозов в БД...")
        rows_loaded = load_predictions_to_db(conn, df_scored, model_name=MODEL_NAME)

    return {
        "total_rows": len(df_scored),
        "actual_rows": int(actual_mask.sum()),
        "future_rows": int(future_mask.sum()),
        "rows_loaded": rows_loaded,
    }
