########################################
# Stage 1 — JS obfuscation
########################################
FROM node:20-alpine AS js-builder

WORKDIR /app

RUN npm install -g javascript-obfuscator

COPY static/js ./static/js

RUN mkdir -p static_dist/js && \
    for f in static/js/*.js; do \
        javascript-obfuscator "$f" \
          --output "static_dist/js/$(basename "$f")" \
          --compact true \
          --control-flow-flattening true \
          --dead-code-injection true \
          --string-array true \
          --string-array-encoding base64 ; \
    done

########################################
# Stage 2 — Python runtime
########################################
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

# Подменяем JS
COPY --from=js-builder /app/static_dist/js /app/static/js

# Накатываем миграции
RUN alembic upgrade head

# Запускаем Uvicorn
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--ws", "websockets", "--timeout-keep-alive", "600", "--proxy-headers", "--forwarded-allow-ips", "*"]
