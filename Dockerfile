# =============================================================================
# Dockerfile для "Столик на троих" v2.0
# 
# Features:
# - Python 3.11 slim
# - Playwright с Chromium
# - Оптимизированная сборка
# =============================================================================

FROM python:3.11-slim as builder

# Установка системных зависимостей для сборки
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Установка Python зависимостей
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir --user -r requirements.txt

# =============================================================================
# Финальный образ
# =============================================================================

FROM python:3.11-slim

# Метаданные
LABEL maintainer="Table for Three Team"
LABEL version="2.0.0"
LABEL description="Мультиагентный чат-рум с несколькими ИИ-моделями"

# Установка системных зависимостей для Playwright
RUN apt-get update && apt-get install -y --no-install-recommends \
    # Playwright dependencies
    libglib2.0-0 \
    libnss3 \
    libnspr4 \
    libatk1.0-0 \
    libatk-bridge2.0-0 \
    libcups2 \
    libdrm2 \
    libdbus-1-3 \
    libxcb1 \
    libxkbcommon0 \
    libx11-6 \
    libxcomposite1 \
    libxdamage1 \
    libxext6 \
    libxfixes3 \
    libxrandr2 \
    libgbm1 \
    libpango-1.0-0 \
    libcairo2 \
    libasound2 \
    libatspi2.0-0 \
    # Дополнительно
    curl \
    wget \
    gnupg \
    fonts-liberation \
    fontconfig \
    && rm -rf /var/lib/apt/lists/* \
    && fc-cache -fv

# Копируем Python пакеты из builder
COPY --from=builder /root/.local /root/.local
ENV PATH=/root/.local/bin:$PATH

# Установка Playwright и браузеров
RUN pip install --no-cache-dir playwright && \
    playwright install chromium && \
    playwright install-deps chromium && \
    rm -rf /root/.cache/pip

# Создаём директории
RUN mkdir -p /app/logs /app/config /app/static

# Копируем код приложения
WORKDIR /app
COPY . .

# Создаём непривилегированного пользователя
RUN useradd -m -u 1000 appuser && \
    chown -R appuser:appuser /app
USER appuser

# Открываем порт
EXPOSE 8000

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

# Запуск приложения
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
