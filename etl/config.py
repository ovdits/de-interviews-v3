"""
Конфигурация: подключение к PostgreSQL и параметры ETL.
Используем psycopg2.
Параметры берутся из переменных окружения или имеют дефолты.
"""

import os
from datetime import date
from pathlib import Path

from dotenv import load_dotenv

# Загружаем .env из корня проекта
load_dotenv()

# --- Подключение к PostgreSQL ---
DB_CONFIG = {
    "host": os.getenv("DB_HOST", "localhost"),
    "port": int(os.getenv("DB_PORT", 5432)),
    "dbname": os.getenv("POSTGRES_DB", "deals_bigdata"),
    "user": os.getenv("POSTGRES_USER", "postgres"),
    "password": os.getenv("POSTGRES_PASSWORD", "postgres"),
}

# --- Параметры ETL ---
# Период по умолчанию: с начала генерации данных до даты снимка
DEFAULT_START_DATE = date(2023, 6, 1)
DEFAULT_END_DATE = date(2026, 10, 26)

# Размер пакета для execute_values (оптимально для 300K строк)
BATCH_SIZE = 10000

# Формат логирования
LOG_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"

# Корень проекта
PROJECT_ROOT = Path(__file__).parent.parent
