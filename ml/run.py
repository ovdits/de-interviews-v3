"""
Точка входа ML-пайплайна.

Запуск:
    python -m ml.run                   # обучение + скоринг
    python -m ml.run --train-only      # только обучение
    python -m ml.run --score-only      # только скоринг
"""

import argparse
import logging
import sys
import os

from etl.config import LOG_FORMAT
from ml.train import run_training
from ml.scoring import run_scoring, load_model
from etl.utils import get_connection

logger = logging.getLogger(__name__)


def main():
    """Парсит аргументы CLI и запускает ML-пайплайн."""
    parser = argparse.ArgumentParser(description="ML-пайплайн: обучение + скоринг")
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Включить логирование DEBUG",
    )
    parser.add_argument(
        "--train-only",
        action="store_true",
        help="Только обучение модели",
    )
    parser.add_argument(
        "--score-only",
        action="store_true",
        help="Только скоринг (без переобучения)",
    )

    args = parser.parse_args()

    # Настраиваем логирование
    level = logging.DEBUG if args.debug else logging.INFO
    logging.basicConfig(level=level, format=LOG_FORMAT)

    try:
        # Обучение
        if not args.score_only:
            logger.info("=" * 60)
            logger.info("Этап 1: Обучение модели")
            logger.info("=" * 60)

            with get_connection() as conn:
                metrics = run_training(conn)

            print("\nМетрики модели:")
            for key, val in metrics.items():
                print(f"  {key}: {val}")

        # Скоринг
        if not args.train_only:
            logger.info("=" * 60)
            logger.info("Этап 2: Batch-скоринг")
            logger.info("=" * 60)

            # Загружаем обученную модель
            model = load_model()

            with get_connection() as conn:
                scoring_result = run_scoring(conn, model)

            print("\nРезультат скоринга:")
            for key, val in scoring_result.items():
                print(f"  {key}: {val}")

        print("\n" + "=" * 60)
        print("ML-пайплайн завершён успешно")
        print("=" * 60)

    except Exception as e:
        logging.error("ML-пайплайн не выполнен: %s", e)
        sys.exit(1)


if __name__ == "__main__":
    main()
