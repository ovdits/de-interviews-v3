"""
Слой Core: трансформация raw.raw_prices -> core.cleaned_prices.

Этапы:
  1. Чтение из raw.raw_prices
  2. DQ-валидация -> dq.dq_quarantine_prices
  3. Конвертация валют в RUB
  4. Обогащение: категории веса/объёма, body_group
  5. Загрузка в core.cleaned_prices
"""

import logging
import time
from datetime import date

import pandas as pd
import psycopg2
from psycopg2.extras import execute_values

from etl.utils import (
    get_connection, make_batch_id, save_etl_run,
    fmt_null, build_copy_sql, copy_from_strings,
    extract_exchange_rates, extract_body_types_mapping,
    extract_price_body_types,
    BODY_GROUP_MAP, OTHER_BODY_GROUP, UNASSIGNED_BODY_GROUP,
)
from etl.config import DB_CONFIG

logger = logging.getLogger(__name__)

# Столбцы core.cleaned_prices — синхронизированы с DDL (raw_core_mart.sql:82-109)
CORE_COLUMNS = [
    "id", "created_at", "updated_at", "subscriber_id", "organization_id",
    "loading_date", "loading_city_gcid", "unloading_city_gcid",
    "distance", "total_weight", "total_volume", "body_type",
    "price", "currency_code", "price_rub", "complex", "price_type",
    "is_fact", "nearest_rate_date", "outlier_mark", "weight_category",
    "volume_category", "price_group_id", "body_group", "etl_batch_id",
    "etl_loaded_at",
]


# ===================================================================
# DQ-валидация (векторизованно через pandas)
# ===================================================================

def apply_dq(df: pd.DataFrame) -> tuple:
    """
    Применение правил качества данных (векторизованно).

    Возвращает (valid_df, quarantine_df).

    Правила:
      1. price > 0
      2. RUB (643): price > 1000
      3. distance > 0
      4. total_weight >= 0
      5. total_volume >= 0

    Оптимизация: вместо .isin() формируем карантин через concat
    нарушителей по каждой причине — O(n) вместо O(n*m).
    """
    # Строим маску каждой причины карантина
    m_price    = df["price"] <= 0
    m_rub_bad  = (df["currency_code"] == "643") & (df["price"] > 0) & (df["price"] <= 1000)
    m_distance = df["distance"] <= 0
    m_weight   = df["total_weight"] < 0
    m_volume   = df["total_volume"] < 0

    # Формируем карантин через concat (O(n), без .isin)
    q_parts = []

    if m_price.any():
        subset = df[m_price]
        q_parts.append(pd.DataFrame({
            "price_id": subset["id"],
            "reason_code": "price_not_positive",
            "reason_detail": f"price={subset['price']}, currency={subset['currency_code']}",
        }))

    if m_rub_bad.any():
        subset = df[m_rub_bad]
        q_parts.append(pd.DataFrame({
            "price_id": subset["id"],
            "reason_code": "rub_price_below_threshold",
            "reason_detail": f"price={subset['price']}",
        }))

    if m_distance.any():
        subset = df[m_distance]
        q_parts.append(pd.DataFrame({
            "price_id": subset["id"],
            "reason_code": "distance_not_positive",
            "reason_detail": f"distance={subset['distance']}",
        }))

    if m_weight.any():
        subset = df[m_weight]
        q_parts.append(pd.DataFrame({
            "price_id": subset["id"],
            "reason_code": "negative_weight",
            "reason_detail": f"weight={subset['total_weight']}",
        }))

    if m_volume.any():
        subset = df[m_volume]
        q_parts.append(pd.DataFrame({
            "price_id": subset["id"],
            "reason_code": "negative_volume",
            "reason_detail": f"volume={subset['total_volume']}",
        }))

    if q_parts:
        quarantine_df = pd.concat(q_parts, ignore_index=True)
        # Дедупликация: одна строка может нарушить несколько правил,
        # оставляем первую причину
        quarantine_df = quarantine_df.drop_duplicates(subset=["price_id"], keep="first")
    else:
        quarantine_df = pd.DataFrame(columns=["price_id", "reason_code", "reason_detail"])

    # Валидные = все кроме нарушителей
    bad_ids = quarantine_df["price_id"].values
    valid_df = df[~df["id"].isin(bad_ids)].copy()

    logger.info("DQ: %d всего, %d прошло, %d в карантин",
                len(df), len(valid_df), len(quarantine_df))
    return valid_df, quarantine_df


# ===================================================================
# Конвертация валют (векторизованно через pandas)
# ===================================================================

def convert_currency(df: pd.DataFrame, rates: dict) -> tuple:
    """
    Конвертация цен в RUB (векторизованно).

    Для RUB (643): price_rub = price
    Для USD/EUR: price_rub = price * rate на nearest_rate_date
    Отсутствующий курс -> карантин

    Возвращает (converted_df, quarantine_df).
    """
    df = df.copy()
    quarantine_rows = []

    # RUB — курс 1.0
    mask_rub = df["currency_code"] == "643"
    df.loc[mask_rub, "price_rub"] = df.loc[mask_rub, "price"]

    # Иностранная валюта
    mask_foreign = ~mask_rub
    if not mask_foreign.any():
        return df, pd.DataFrame(columns=["price_id", "reason_code", "reason_detail"])

    # Векторизованный lookup: создаём ключи (currency_code, nearest_rate_date)
    foreign_df = df.loc[mask_foreign].copy()
    foreign_df["rate_key"] = (
        foreign_df["currency_code"] + "|" +
        foreign_df["nearest_rate_date"].fillna("NULL").astype(str)
    )

    # Строим lookup: key -> rate
    rates_lookup = {}
    for (code, rdate), val in rates.items():
        rates_lookup[f"{code}|{rdate}"] = val

    # Применяем lookup векторизованно
    foreign_df["rate"] = foreign_df["rate_key"].map(rates_lookup)

    # Строки без курса -> карантин
    mask_no_rate = foreign_df["rate"].isna()
    if mask_no_rate.any():
        no_rate = foreign_df[mask_no_rate]
        quarantine_rows.extend([
            (int(pid), "currency_rate_not_found",
             f"currency={cur}, nearest_rate_date={rdate}")
            for pid, cur, rdate in zip(
                no_rate["id"], no_rate["currency_code"],
                no_rate["nearest_rate_date"].fillna("NULL").astype(str)
            )
        ])

    # Строки с курсом -> конвертируем
    mask_has_rate = ~mask_no_rate
    if mask_has_rate.any():
        has_rate = foreign_df[mask_has_rate]
        df.loc[has_rate.index, "price_rub"] = (
            has_rate["price"] * has_rate["rate"]
        )

    quarantine_df = pd.DataFrame(
        quarantine_rows,
        columns=["price_id", "reason_code", "reason_detail"]
    )

    # Удаляем строки без конвертации
    if not quarantine_df.empty:
        df = df[~df["id"].isin(quarantine_df["price_id"])]

    logger.info("Валюта: %d конвертировано, %d в карантин",
                len(df) - len(quarantine_df), len(quarantine_df))
    return df, quarantine_df


# ===================================================================
# Обогащение (векторизованно через pandas)
# ===================================================================

def enrich_categories(df: pd.DataFrame) -> pd.DataFrame:
    """Заполнение пропущенных weight_category и volume_category."""
    df = df.copy()

    # Весовая категория
    mask_w = df["weight_category"].isna()
    if mask_w.any():
        w = df.loc[mask_w, "total_weight"].round(1)
        df.loc[mask_w, "weight_category"] = pd.cut(
            w,
            bins=[-float("inf"), 0.5, 2.5, 5.0, 10.0, 30.0, float("inf")],
            labels=["OTH", "L5T", "5T", "10T", "FTL", "G20T"],
        )

    # Объёмная категория
    mask_v = df["volume_category"].isna()
    if mask_v.any():
        v = df.loc[mask_v, "total_volume"]
        df.loc[mask_v, "volume_category"] = v.apply(
            lambda val: "FTL" if 0 < val <= 120 else "OTH"
        )

    return df


def enrich_body_group(df: pd.DataFrame, pbt_map: dict, std_map: dict) -> pd.DataFrame:
    """Определение body_group для каждой сделки."""
    df = df.copy()
    df["body_group"] = UNASSIGNED_BODY_GROUP

    # Приоритет 1: price_body_types -> минимальный std_id
    if pbt_map:
        pbt_series = df["id"].map(pbt_map)
        mask_pbt = pbt_series.notna()
        df.loc[mask_pbt, "body_group"] = pbt_series[mask_pbt].map(
            lambda sid: BODY_GROUP_MAP.get(sid, OTHER_BODY_GROUP)
        )

    # Приоритет 2: prices.body_type
    mask_no_group = df["body_group"] == UNASSIGNED_BODY_GROUP
    if mask_no_group.any():
        df.loc[mask_no_group, "body_group"] = (
            df.loc[mask_no_group, "body_type"]
            .map(std_map)
            .map(BODY_GROUP_MAP)
        )
        df.loc[mask_no_group & df["body_group"].isna(), "body_group"] = OTHER_BODY_GROUP

    logger.info("Группы кузовов: %s", df["body_group"].value_counts().to_dict())
    return df


# ===================================================================
# Загрузка в core
# ===================================================================

def load_to_core(conn, df: pd.DataFrame, batch_id: str,
                 start_date: date, end_date: date) -> int:
    """
    Идемпотентная загрузка в core.cleaned_prices.

    DELETE+INSERT в одной транзакции.
    """
    cursor = conn.cursor()

    # Удаляем старые данные за период
    cursor.execute(
        "DELETE FROM core.cleaned_prices WHERE loading_date >= %s AND loading_date <= %s",
        (start_date, end_date),
    )
    logger.info("Удалены старые данные core за %s — %s", start_date, end_date)

    if df.empty:
        logger.info("Нет строк для загрузки в core")
        return 0

    # Подготавливаем DataFrame для COPY
    copy_df = df[CORE_COLUMNS[:-1]].copy()  # без etl_loaded_at
    copy_df["etl_batch_id"] = batch_id
    copy_df["etl_loaded_at"] = pd.Timestamp.now()

    # Преобразуем в строки для COPY
    copy_rows = []
    for row in copy_df.itertuples(index=False):
        vals = []
        for val in row:
            vals.append(fmt_null(val))
        copy_rows.append("\t".join(vals))

    copy_sql = build_copy_sql("core.cleaned_prices", CORE_COLUMNS)
    copy_from_strings(cursor, copy_sql, copy_rows)

    logger.info("Загружено %d строк в core.cleaned_prices", len(copy_rows))
    return len(copy_rows)


def load_quarantine(conn, quarantine_df: pd.DataFrame, batch_id: str) -> int:
    """Загрузка карантинных записей в dq.dq_quarantine_prices."""
    if quarantine_df.empty:
        return 0

    cursor = conn.cursor()
    execute_values(
        cursor,
        """
        INSERT INTO dq.dq_quarantine_prices
            (price_id, reason_code, reason_detail, batch_id)
        VALUES %s
        """,
        [
            (int(r["price_id"]), r["reason_code"], r["reason_detail"], batch_id)
            for _, r in quarantine_df.iterrows()
        ],
        page_size=10000,
    )
    logger.info("Загружено %d записей в карантин", len(quarantine_df))
    return len(quarantine_df)


# ===================================================================
# Главный пайплайн
# ===================================================================

def run_core_etl(start_date: date, end_date: date) -> dict:
    """
    Запуск ETL core-слоя: raw -> core.

    Этапы:
      1. Чтение из raw.raw_prices
      2. DQ-валидация
      3. Конвертация валют
      4. Обогащение (категории, body_group)
      5. Загрузка в core.cleaned_prices
      6. Загрузка карантина
      7. Сохранение метаданных
    """
    batch_id = make_batch_id("core", start_date, end_date)

    logger.info("=" * 60)
    logger.info("Core ETL запущен (батч: %s)", batch_id)
    logger.info("Период: %s — %s", start_date, end_date)

    t0 = time.time()

    # Переменные для метрик (вне with, чтобы избежать scope-проблем)
    rows_read = 0
    rows_valid = 0
    rows_quarantined = 0
    rows_written = 0

    with get_connection() as conn:
        # Этап 1: Чтение из raw
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT id, created_at, updated_at, subscriber_id, organization_id,
                   loading_date, loading_city_gcid, unloading_city_gcid,
                   distance, total_weight, total_volume, body_type,
                   price, currency_code, complex, price_type, item_id,
                   is_fact, nearest_rate_date, meta_item_id, end_loading_date,
                   source, external_item_uuid, outlier_mark,
                   weight_category, volume_category, price_group_id
            FROM raw.raw_prices
            WHERE loading_date >= %s AND loading_date <= %s
            ORDER BY id
            """,
            (start_date, end_date),
        )
        raw_rows = cursor.fetchall()
        # Столбцы raw.raw_prices (29 полей, но нас интересуют только source-поля)
        raw_columns = [
            "id", "created_at", "updated_at", "subscriber_id", "organization_id",
            "loading_date", "loading_city_gcid", "unloading_city_gcid",
            "distance", "total_weight", "total_volume", "body_type",
            "price", "currency_code", "complex", "price_type", "item_id",
            "is_fact", "nearest_rate_date", "meta_item_id", "end_loading_date",
            "source", "external_item_uuid", "outlier_mark",
            "weight_category", "volume_category", "price_group_id",
        ]
        df = pd.DataFrame(raw_rows, columns=raw_columns)
        rows_read = len(df)
        logger.info("Прочитано %d строк из raw.raw_prices", rows_read)

        # Этап 2: DQ-валидация
        valid_df, dq_quarantine = apply_dq(df)
        rows_valid = len(valid_df)

        # Этап 3: Конвертация валют
        rates = extract_exchange_rates(conn)
        converted_df, currency_quarantine = convert_currency(valid_df, rates)
        all_quarantine = pd.concat([dq_quarantine, currency_quarantine], ignore_index=True)

        # Этап 4: Обогащение
        enriched_df = enrich_categories(converted_df)
        final_df = enrich_body_group(enriched_df,
                                     extract_price_body_types(conn),
                                     extract_body_types_mapping(conn))

        # Этап 5: Загрузка в core
        rows_written = load_to_core(conn, final_df, batch_id, start_date, end_date)

        # Этап 6: Загрузка карантина
        rows_quarantined = load_quarantine(conn, all_quarantine, batch_id)

        # Этап 7: Сохранение метаданных
        save_etl_run(
            conn, "etl_core", batch_id,
            start_date, end_date,
            rows_read=rows_read,
            rows_written=rows_written,
            rows_quarantined=rows_quarantined,
            status="success",
        )

    duration = time.time() - t0
    logger.info("Core ETL завершён за %.1fs: %d прочитано, %d прошло DQ, %d в карантине, %d записано",
                duration, rows_read, rows_valid, rows_quarantined, rows_written)

    return {
        "batch_id": batch_id,
        "rows_read": rows_read,
        "rows_valid": rows_valid,
        "rows_quarantined": rows_quarantined,
        "rows_written": rows_written,
        "duration_sec": round(duration, 2),
    }
