FROM python:3.11

WORKDIR /app

# Устанавливаем Poetry
RUN pip install --no-cache-dir poetry

# Копируем файлы конфигурации Poetry
COPY pyproject.toml poetry.lock ./

# Устанавливаем зависимости глобально, без виртуального окружения, с флагом --no-root
RUN poetry config virtualenvs.create false \
    && poetry install --no-root --no-interaction --no-ansi

# Копируем остальной код проекта
COPY . .

# Накатываем миграции
RUN alembic upgrade head

# Запускаем Uvicorn
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
