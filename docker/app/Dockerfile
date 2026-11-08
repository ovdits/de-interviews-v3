FROM python:3.11-slim

# Устанавливаем зависимости ОС для psycopg2
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc libpq-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Копируем зависимости и устанавливаем их
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Копируем весь проект
COPY . .

# Переменные окружения по умолчанию
ENV PYTHONUNBUFFERED=1
ENV DB_HOST=db
ENV DB_PORT=5432
ENV DB_NAME=deals_bigdata
ENV DB_USER=postgres
ENV DB_PASSWORD=postgres

# Команда по умолчанию — полный пайплайн
CMD ["python", "-m", "etl.run", "--layers", "raw,core,mart,dimensions"]
