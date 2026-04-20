# 💬 Столик на троих v2.0

Мультиагентный чат-рум с несколькими ИИ-моделями, модерацией контента и расширяемой архитектурой.

## ✨ Что нового в v2.0

### Исправления критических проблем
- ✅ **Единый Playwright** — все WebChatAdapter используют один экземпляр браузера
- ✅ **Пул HTTP-клиентов** — переиспользование соединений вместо создания на каждый запрос
- ✅ **JSON-логирование диалогов** — полный аудит всех разговоров в `logs/dialogs.jsonl`
- ✅ **Graceful shutdown** — корректное закрытие всех компонентов
- ✅ **Pydantic Settings** — валидация конфигурации через переменные окружения

### Улучшения
- 🎨 Обновлённый UI с тёмной темой
- 📊 Расширенный health-check со статистикой по каждой модели
- 🔧 Улучшенная обработка ошибок
- 🐳 Оптимизированный Dockerfile

## 🏗️ Архитектура

```
┌─────────────┐     WebSocket      ┌─────────────────┐
│   Client    │ ◄────────────────► │   FastAPI App   │
│ (index.html)│                    │    (main.py)    │
└─────────────┘                    └────────┬────────┘
                                            │
                    ┌───────────────────────┼───────────────────────┐
                    ▼                       ▼                       ▼
            ┌─────────────┐        ┌─────────────┐          ┌─────────────┐
            │   Buffer    │        │  Moderator  │          │   Registry  │
            │(ResponseBuf)│        │(ContentMod) │          │(ModelReg)   │
            └──────┬──────┘        └─────────────┘          └──────┬──────┘
                   │                                               │
                   │                              ┌────────────────┼────────────────┐
                   │                              ▼                ▼                ▼
                   └──────────────────────►┌──────────┐      ┌──────────┐     ┌──────────┐
                                           │ API Adpt │      │WebChatAdp│     │WebChatAdp│
                                           │(OpenAI)  │      │(Browser) │     │(Browser) │
                                           └──────────┘      └──────────┘     └──────────┘
```

## 🚀 Быстрый старт

### 1. Клонирование и настройка

```bash
git clone <repo-url>
cd table-for-three-v2

# Скопируйте пример переменных окружения
cp .env.example .env

# Отредактируйте .env, добавьте свои API ключи
nano .env
```

### 2. Запуск через Docker (рекомендуется)

```bash
# Сборка и запуск
docker-compose up --build

# Или в фоновом режиме
docker-compose up -d --build
```

### 3. Локальный запуск

```bash
# Установка зависимостей
pip install -r requirements.txt

# Установка Playwright браузеров
playwright install chromium

# Запуск
uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

### 4. Проверка

- **UI**: http://localhost:8000/static/index.html
- **API Docs**: http://localhost:8000/docs
- **Health**: http://localhost:8000/health

## ⚙️ Конфигурация

### Переменные окружения (.env)

```bash
# API Keys
OPENAI_API_KEY=sk-your-key
ANTHROPIC_API_KEY=sk-your-key
OPENAI_MODERATION_KEY=sk-your-key

# Настройки
APP_PORT=8000
LOG_LEVEL=INFO
RESPONSE_TIMEOUT=10.0
RESPONSE_STRATEGY=compare
```

### Конфигурация моделей (config/models.json)

```json
{
  "models": [
    {
      "id": "gpt-4",
      "type": "api",
      "name": "gpt-4",
      "endpoint": "https://api.openai.com/v1/chat/completions",
      "api_key": "${OPENAI_API_KEY}",
      "timeout": 30.0
    },
    {
      "id": "webchat-demo",
      "type": "webchat",
      "url": "https://example.com/chat",
      "selectors": {
        "input": "textarea",
        "submit": "button",
        "output": "div.response"
      }
    }
  ]
}
```

## 🧪 Тестирование

```bash
# Установка зависимостей для тестов
pip install pytest pytest-asyncio httpx websockets

# Запуск тестов (требует запущенный сервер для WebSocket тестов)
pytest tests/ -v

# Запуск только unit-тестов
pytest tests/ -v -k "not websocket"
```

## 📊 Мониторинг

### Health Check

```bash
curl http://localhost:8000/health
```

Ответ:
```json
{
  "status": "healthy",
  "models_loaded": 3,
  "active_requests": 0,
  "success_rate": 0.95,
  "avg_response_time_global": 2.3,
  "model_statuses": {
    "gpt-4": true,
    "claude": true,
    "local-llm": false
  },
  "model_avg_response_time": {
    "gpt-4": 1.8,
    "claude": 2.5,
    "local-llm": null
  }
}
```

### Логи диалогов

Все диалоги сохраняются в `logs/dialogs.jsonl`:

```json
{
  "timestamp": 1712345678.123,
  "request_id": "req_1712345678_12345",
  "user_message": "Привет!",
  "responses": {
    "gpt-4": "Привет! Как дела?",
    "claude": "Здравствуй! Чем могу помочь?"
  },
  "elapsed_seconds": 2.345
}
```

## 🔧 Стратегии ответов

Настраивается через `RESPONSE_STRATEGY`:

- **`concat`** — все ответы подряд
- **`first`** — только первый ответ
- **`compare`** — сравнение с разделителями (по умолчанию)
- **`best`** — выбор "лучшего" ответа по эвристикам

## 🛡️ Модерация

### Уровни модерации

1. **Pre-moderation** — фильтрация входящих запросов
2. **Post-moderation** — фильтрация ответов моделей
3. **Identity protection** — скрытие "Я — ИИ"
4. **PII redaction** — маскирование email/телефонов

### Настройка

```bash
# Порог чувствительности (0.0 - 1.0)
MODERATION_THRESHOLD=0.7

# Включение/выключение
APP_MODERATION__PRE_MODERATION_ENABLED=true
APP_MODERATION__POST_MODERATION_ENABLED=true
APP_MODERATION__PII_REDACTION_ENABLED=true
```

## 🐳 Docker

### Сборка

```bash
docker build -t table-for-three:v2.0 .
```

### Запуск

```bash
docker run -d \
  -p 8000:8000 \
  -e OPENAI_API_KEY=sk-your-key \
  -v $(pwd)/logs:/app/logs \
  -v $(pwd)/config:/app/config:ro \
  table-for-three:v2.0
```

## 📁 Структура проекта

```
table-for-three-v2/
├── main.py                    # Точка входа FastAPI
├── config/
│   ├── settings.py            # Pydantic Settings
│   ├── models.json            # Конфигурация моделей
│   └── workflow.json          # Настройки workflow
├── utils/
│   ├── __init__.py
│   ├── buffer.py              # ResponseBuffer с логированием
│   ├── moderation.py          # ContentModerator
│   ├── models.py              # ModelRegistry + адаптеры
│   ├── sync.py                # Форматирование ответов
│   └── webchat_analyzer.py    # Анализатор веб-чатов
├── static/
│   └── index.html             # UI
├── tests/
│   └── test_integration.py    # Тесты
├── Dockerfile
├── docker-compose.yml
├── requirements.txt
├── .env.example
└── README.md
```

## 📝 Лицензия

MIT License

## 🤝 Contributing

PR приветствуются! Пожалуйста, следуйте code style и добавляйте тесты.
