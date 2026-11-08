"""
Модуль для запуска ETL-пайплайна через python -m etl.
"""

import sys
from pathlib import Path

from etl.run import main

# Добавляем корень проекта в sys.path
project_root = Path(__file__).parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))


if __name__ == "__main__":
    main()
