"""
Конфигурация ML-модуля.

Параметры модели, seed, даты train/test сплита.
"""

import logging
from datetime import date

logger = logging.getLogger(__name__)

# Имя модели
MODEL_NAME = "catboost_v1"

# Фиксированное зерно для воспроизводимости
RANDOM_SEED = 42

# Даты временного сплита (as_of = 2026-10-26)
AS_OF_DATE = date(2026, 10, 26)
TRAIN_START = date(2026, 7, 26)   # as_of - 3 месяца
TRAIN_END = date(2026, 10, 5)     # as_of - 3 недели (не входит в test)
TEST_START = date(2026, 10, 5)    # граница test
TEST_END = date(2026, 10, 26)     # as_of (включительно)

# Параметры CatBoost
CATBOOST_PARAMS = {
    "iterations": 1000,
    "depth": 6,
    "learning_rate": 0.05,
    "loss_function": "RMSE",
    "random_seed": RANDOM_SEED,
    "verbose": 100,
    "use_best_model": True,
    "eval_metric": "RMSE",
}

# Категориальные признаки
CAT_FEATURES = [
    "origin_region",
    "destination_region",
    "body_group",
    "weight_category",
    "volume_category",
    "origin_city_encoded",
    "destination_city_encoded",
]

# Числовые признаки (без целевой переменной и leakage-признаков)
NUM_FEATURES = [
    "distance",
    "total_weight",
    "total_volume",
    "sin_month", "cos_month",
    "sin_dow", "cos_dow",
    "sin_doy", "cos_doy",
    "origin_lat", "origin_lon",
    "dest_lat", "dest_lon",
]

# Целевая переменная
TARGET = "price_rub"

# Признаки-утечки (запрещены!)
LEAKAGE_FEATURES = [
    "price_with_vat",
    "price_group_id",
    "avg_price",
    "min_km_price",
    "max_km_price",
    "price_group_stats",
]

logger.info("ML-конфиг загружен: seed=%d, train=[%s, %s), test=[%s, %s]",
            RANDOM_SEED, TRAIN_START, TRAIN_END, TEST_START, TEST_END)
