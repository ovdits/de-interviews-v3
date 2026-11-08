"""
Feature Engineering для ML-модели прогнозирования тарифов.

Извлекает гео-признаки, циклическое кодирование дат,
фильтрует leakage-признаки.
"""

import logging
from datetime import date

import pandas as pd
import numpy as np

from etl.utils import get_connection
from ml.config import (
    AS_OF_DATE, TRAIN_START, TRAIN_END, TEST_START, TEST_END,
    NUM_FEATURES, CAT_FEATURES, TARGET, LEAKAGE_FEATURES,
)

logger = logging.getLogger(__name__)


def extract_features(conn) -> pd.DataFrame:
    """
    Извлечение сырых данных для ML из core.cleaned_prices + dim_cities.

    Возвращает DataFrame с исходными признаками (до трансформаций).
    """
    query = """
        SELECT
            cp.id as price_id,
            cp.loading_date,
            cp.loading_city_gcid,
            cp.unloading_city_gcid,
            cp.distance,
            cp.total_weight,
            cp.total_volume,
            cp.body_group,
            cp.weight_category,
            cp.volume_category,
            cp.price_rub,
            cp.price_type,
            cp.is_fact,
            cp.complex,
            dc_origin.full_name_local as origin_full_name,
            dc_origin.latitude as origin_lat,
            dc_origin.longitude as origin_lon,
            dc_dest.full_name_local as dest_full_name,
            dc_dest.latitude as dest_lat,
            dc_dest.longitude as dest_lon,
            dc_origin.region as origin_region,
            dc_dest.region as dest_region
        FROM core.cleaned_prices cp
        LEFT JOIN mart.dim_cities dc_origin
            ON dc_origin.gc_id = cp.loading_city_gcid
        LEFT JOIN mart.dim_cities dc_dest
            ON dc_dest.gc_id = cp.unloading_city_gcid
        WHERE cp.is_fact = true
          AND cp.complex = false
          AND cp.price_rub IS NOT NULL
          AND cp.price_rub > 0
          AND cp.distance > 0
          AND cp.outlier_mark = false
        ORDER BY cp.loading_date
    """

    df = pd.read_sql(query, conn)
    logger.info("Извлечено %d строк для ML-признаков", len(df))
    return df


def extract_features_for_scoring(conn) -> pd.DataFrame:
    """
    Извлечение данных для скоринга (включая будущие заявки).

    Для будущих заявок price_rub будет NULL — это planned orders.
    """
    query = """
        SELECT
            cp.id as price_id,
            cp.loading_date,
            cp.loading_city_gcid,
            cp.unloading_city_gcid,
            cp.distance,
            cp.total_weight,
            cp.total_volume,
            cp.body_group,
            cp.weight_category,
            cp.volume_category,
            cp.price_rub,
            cp.price_type,
            cp.is_fact,
            cp.complex,
            dc_origin.full_name_local as origin_full_name,
            dc_origin.latitude as origin_lat,
            dc_origin.longitude as origin_lon,
            dc_dest.full_name_local as dest_full_name,
            dc_dest.latitude as dest_lat,
            dc_dest.longitude as dest_lon,
            dc_origin.region as origin_region,
            dc_dest.region as dest_region
        FROM core.cleaned_prices cp
        LEFT JOIN mart.dim_cities dc_origin
            ON dc_origin.gc_id = cp.loading_city_gcid
        LEFT JOIN mart.dim_cities dc_dest
            ON dc_dest.gc_id = cp.unloading_city_gcid
        WHERE cp.price_rub IS NOT NULL
          AND cp.distance > 0
          AND cp.outlier_mark = false
        ORDER BY cp.loading_date
    """

    df = pd.read_sql(query, conn)
    logger.info("Извлечено %d строк для скоринга (вкл. будущие)", len(df))
    return df


def engineer_features(df: pd.DataFrame, train_df: pd.DataFrame = None) -> pd.DataFrame:
    """
    Создание признаков из сырых данных.

    Этапы:
      1. Извлечение регионов из full_name_local
      2. Кодирование городов (encounter-based для редких направлений)
         — частоты считаются ТОЛЬКО на train-выборке (защита от утечки)
      3. Циклическое кодирование дат (sin/cos)
      4. Фильтрация leakage-признаков

    Args:
        df: DataFrame из extract_features()
        train_df: train-выборка для расчёта частот (если None — считаем на df)

    Returns:
        DataFrame с инженерными признаками
    """
    df = df.copy()

    # 1. Извлечение регионов (если ещё не извлечены)
    if "origin_region" not in df.columns or df["origin_region"].isna().any():
        # Векторизованное извлечение через str.split (в 10x быстрее apply)
        for name_col, region_col in [
            ("origin_full_name", "origin_region"),
            ("dest_full_name", "dest_region"),
        ]:
            if name_col not in df.columns or region_col not in df.columns:
                continue
            # Заполняем NaN строкой-заглушкой для split
            filled = df[name_col].fillna("Не указан, Не указан")
            parts = filled.str.split(",", n=1, expand=True)
            # Часть после запятой — регион, часть до — город
            regions = parts.iloc[:, 1].str.strip()
            # Если split не дал вторую часть (нет запятой) — "Не указан"
            regions = regions.where(regions != "", "Не указан")
            df[region_col] = regions

    # 2. Кодирование городов (encounter-based: частые -> название, редкие -> "Other")
    # КРИТИЧНО: частоты считаем только на train-выборке, чтобы избежать утечки
    # из test-данных в train-признаки
    for col_prefix in ["origin", "dest"]:
        city_col = f"{col_prefix}_city_encoded"
        region_col = f"{col_prefix}_region"
        if region_col not in df.columns:
            continue

        if train_df is not None and region_col in train_df.columns:
            # Считаем частоты ТОЛЬКО на train-выборке
            city_counts = train_df[region_col].value_counts()
            frequent_cities = city_counts[city_counts >= 5].index
        else:
            # Fallback: считаем на текущем df (для скоринга без train)
            city_counts = df[region_col].value_counts()
            frequent_cities = city_counts[city_counts >= 5].index

        df[city_col] = df[region_col].where(
            df[region_col].isin(frequent_cities),
            "Other"
        )

    # 3. Циклическое кодирование дат
    df["loading_date"] = pd.to_datetime(df["loading_date"])

    # День года (T=365.25)
    df["day_of_year"] = df["loading_date"].dt.dayofyear.astype(float)
    df["sin_doy"] = np.sin(2 * np.pi * df["day_of_year"] / 365.25)
    df["cos_doy"] = np.cos(2 * np.pi * df["day_of_year"] / 365.25)

    # Месяц (T=12)
    df["month"] = df["loading_date"].dt.month.astype(float)
    df["sin_month"] = np.sin(2 * np.pi * df["month"] / 12)
    df["cos_month"] = np.cos(2 * np.pi * df["month"] / 12)

    # День недели (T=7)
    df["dow"] = df["loading_date"].dt.dayofweek.astype(float)
    df["sin_dow"] = np.sin(2 * np.pi * df["dow"] / 7)
    df["cos_dow"] = np.cos(2 * np.pi * df["dow"] / 7)

    # 4. Удаление leakage-признаков
    leakage_cols = [c for c in LEAKAGE_FEATURES if c in df.columns]
    if leakage_cols:
        logger.warning("Удалены leakage-признаки: %s", leakage_cols)
        df = df.drop(columns=leakage_cols)

    # 5. Удаление служебных колонок
    drop_cols = [c for c in ["origin_full_name", "dest_full_name"] if c in df.columns]
    df = df.drop(columns=drop_cols)

    logger.info("Создано признаков: %d числовых, %d категориальных",
                len([c for c in NUM_FEATURES if c in df.columns]),
                len([c for c in CAT_FEATURES if c in df.columns]))

    return df


def split_time_series(df: pd.DataFrame) -> tuple:
    """
    Хронологическое разбиение train/test.

    train: [TRAIN_START, TRAIN_END) = [2026-07-26, 2026-10-05)
    test:  [TEST_START, TEST_END]   = [2026-10-05, 2026-10-26]

    shuffle=False — обязательно для временных данных!
    """
    df = df.copy()
    df["loading_date"] = pd.to_datetime(df["loading_date"])

    train_mask = (df["loading_date"] >= TRAIN_START) & (df["loading_date"] < TRAIN_END)
    test_mask = (df["loading_date"] >= TEST_START) & (df["loading_date"] <= TEST_END)

    df_train = df[train_mask].copy()
    df_test = df[test_mask].copy()

    logger.info("Time-Based Split: train=%d, test=%d", len(df_train), len(df_test))

    return df_train, df_test


def prepare_X_y(df: pd.DataFrame, target_col: str = TARGET) -> tuple:
    """
    Подготовка признаков X и целевой переменной y.

    Args:
        df: DataFrame с инженерными признаками
        target_col: имя целевой колонки

    Returns:
        (X_train, y_train) или (X_test, y_test)
    """
    feature_cols = NUM_FEATURES + CAT_FEATURES
    available_cols = [c for c in feature_cols if c in df.columns]

    X = df[available_cols].copy()
    y = df[target_col].copy() if target_col in df.columns else None

    # Заполнение пропусков
    for col in NUM_FEATURES:
        if col in X.columns:
            X[col] = X[col].fillna(X[col].median())

    logger.info("X shape: %s, y shape: %s", X.shape, y.shape if y is not None else "None")
    return X, y
