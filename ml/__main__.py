"""
Модуль для запуска ML-пайплайна через python -m ml.
"""

import sys
from pathlib import Path

# Добавляем корень проекта в sys.path
project_root = Path(__file__).parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from ml.run import main

if __name__ == "__main__":
    main()
