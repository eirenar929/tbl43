"""
Столик на троих v2.0 - Мультиагентный чат-рум

Исправления:
- Единый Playwright для всех WebChatAdapter
- Пул HTTP-клиентов
- JSON-логирование диалогов
- Graceful shutdown для всех компонентов
- Pydantic Settings для валидации конфигов

Architecture:
- FastAPI + WebSocket
- ModelRegistry с единым Playwright
- ResponseBuffer с логированием
- ContentModerator (pre/post/PII)
"""

import asyncio
import json
import logging
import os
import sys
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Dict, Any, Optional

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

# Конфигурация должна быть импортирована первой
from config.settings import settings

# Настройка логирования ДО импорта других модулей
logging.basicConfig(
    level=getattr(logging, settings.logging.level.upper()),
    format=settings.logging.format,
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.StreamHandler(sys.stdout),
    ]
)
logger = logging.getLogger("TableForThree")

# Импорт утилит
from utils.buffer import ResponseBuffer
from utils.moderation import ContentModerator
from utils.models import ModelRegistry
from utils.sync import format_responses
from utils.webchat_analyzer import WebChatAnalyzer


# ============================================================================
# Pydantic Models для API
# ============================================================================

class WebChatAnalysisRequest(BaseModel):
    """Запрос на анализ веб-чата."""
    url: str = Field(..., description="URL веб-чата для анализа")
    test_prompt: str = Field(default="Тестовый запрос", description="Тестовый промпт")


class HealthResponse(BaseModel):
    """Ответ эндпоинта health-check."""
    status: str
    models_loaded: int
    active_requests: int
    success_rate: float
    avg_response_time_global: float
    model_statuses: Dict[str, bool]
    model_avg_response_time: Dict[str, Optional[float]]


# ============================================================================
# Глобальные компоненты (инициализируются в lifespan)
# ============================================================================

model_registry: Optional[ModelRegistry] = None
response_buffer: Optional[ResponseBuffer] = None
content_moderator: Optional[ContentModerator] = None
webchat_analyzer: Optional[WebChatAnalyzer] = None

models_config: Dict[str, Any] = {}
workflow_config: Dict[str, Any] = {}


# ============================================================================
# Загрузка конфигураций
# ============================================================================

def load_configurations() -> None:
    """Загрузка всех конфигураций из JSON файлов."""
    global workflow_config, models_config
    
    config_dir = settings.config_dir
    
    try:
        # workflow.json
        workflow_path = config_dir / "workflow.json"
        if workflow_path.exists():
            with open(workflow_path, "r", encoding="utf-8") as f:
                workflow_config = json.load(f)
            logger.info(f"[Config] Загружен workflow.json")
        else:
            logger.warning(f"[Config] workflow.json не найден, используем defaults")
            workflow_config = {"settings": {}}
            
        # models.json
        models_path = config_dir / "models.json"
        if models_path.exists():
            with open(models_path, "r", encoding="utf-8") as f:
                models_config = json.load(f)
            models_count = len(models_config.get("models", []))
            logger.info(f"[Config] Загружен models.json ({models_count} моделей)")
        else:
            logger.warning(f"[Config] models.json не найден!")
            models_config = {"models": []}
            
    except json.JSONDecodeError as e:
        logger.error(f"[Config] Ошибка парсинга JSON: {e}")
        raise
    except Exception as e:
        logger.error(f"[Config] Ошибка загрузки конфигов: {e}")
        raise


async def initialize_components() -> None:
    """Инициализация всех компонентов системы."""
    global model_registry, response_buffer, content_moderator, webchat_analyzer
    
    logger.info("[Init] Инициализация компонентов...")
    
    # Создаём ResponseBuffer с JSON-логированием
    response_buffer = ResponseBuffer(
        timeout=workflow_config.get("settings", {}).get("response_timeout", 8.0),
        min_responses=workflow_config.get("settings", {}).get("min_responses", 1),
        dialog_log_path=settings.logging.dialog_log_path
    )
    logger.info("[Init] ResponseBuffer создан")
    
    # Создаём ModelRegistry и инициализируем
    model_registry = ModelRegistry(models_config)
    await model_registry.initialize()  # Запускает единый Playwright
    model_registry.attach_buffer(response_buffer)
    logger.info(f"[Init] ModelRegistry инициализирован ({len(model_registry.adapters)} адаптеров)")
    
    # Создаём ContentModerator
    content_moderator = ContentModerator()
    if settings.moderation.openai_api_key:
        content_moderator.set_openai_api_key(settings.moderation.openai_api_key)
    logger.info("[Init] ContentModerator создан")
    
    # Создаём WebChatAnalyzer с единым Playwright из Registry
    webchat_analyzer = WebChatAnalyzer(
        playwright=model_registry._playwright,
        browser=model_registry._browser
    )
    logger.info("[Init] WebChatAnalyzer создан")
    
    logger.info("[Init] Все компоненты инициализированы")


async def shutdown_components() -> None:
    """Graceful shutdown всех компонентов."""
    logger.info("[Shutdown] Начинаем graceful shutdown...")
    
    # Закрываем ModelRegistry (включая единый Playwright)
    if model_registry:
        await model_registry.close_all()
        logger.info("[Shutdown] ModelRegistry закрыт")
    
    # Закрываем WebChatAnalyzer
    if webchat_analyzer:
        await webchat_analyzer.close()
        logger.info("[Shutdown] WebChatAnalyzer закрыт")
    
    # Очистка ResponseBuffer
    if response_buffer:
        await response_buffer.cleanup()
        logger.info("[Shutdown] ResponseBuffer очищен")
    
    logger.info("[Shutdown] Graceful shutdown завершён")


# ============================================================================
# Lifespan контекст
# ============================================================================

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifespan контекст для startup/shutdown событий."""
    # Startup
    try:
        settings.ensure_directories()
        load_configurations()
        await initialize_components()
        logger.info("✅ Система 'Столик на троих' успешно запущена!")
    except Exception as e:
        logger.error(f"❌ Ошибка запуска: {e}", exc_info=True)
        raise
    
    yield
    
    # Shutdown
    await shutdown_components()
    logger.info("👋 Система 'Столик на троих' остановлена")


# ============================================================================
# Создание FastAPI приложения
# ============================================================================

app = FastAPI(
    title="Столик на троих",
    description="Мультиагентный чат-рум с несколькими ИИ-моделями",
    version="2.0.0",
    lifespan=lifespan
)

# Монтируем статические файлы
app.mount("/static", StaticFiles(directory=str(settings.static_dir)), name="static")


# ============================================================================
# WebSocket обработчик
# ============================================================================

def create_response_handler(
    websocket: WebSocket,
    request_id: str,
    user_message: str
) -> callable:
    """
    Создать обработчик ответов для конкретного запроса.
    
    Args:
        websocket: WebSocket соединение
        request_id: ID запроса
        user_message: Исходное сообщение пользователя
        
    Returns:
        Асинхронная функция-обработчик
    """
    async def handle_responses(responses: Dict[str, Dict[str, Any]]) -> None:
        try:
            # Обрабатываем каждый ответ через модерацию
            processed_responses = {}
            for model_id, response_data in responses.items():
                response = response_data.get("response", "")
                
                # Post-модерация
                moderated = await content_moderator.apply_post_moderation(response)
                
                # Защита идентичности
                protected = content_moderator.apply_identity_protection(
                    moderated,
                    workflow_config.get("identity_protection", {}).get("rules", [])
                )
                
                # PII redaction
                final = content_moderator.redact_pii(protected)
                
                processed_responses[model_id] = {
                    "response": final,
                    "timestamp": response_data.get("timestamp", time.time())
                }
            
            # Форматируем ответы
            combined = format_responses(
                processed_responses,
                workflow_config,
                models_config
            )
            
            # Отправляем пользователю
            await websocket.send_json({
                "type": "response",
                "request_id": request_id,
                "content": combined,
                "responses": {k: v["response"] for k, v in processed_responses.items()},
                "user_message": user_message
            })
            
            logger.info(f"[WS] Ответ на запрос #{request_id} отправлен")
            
        except Exception as e:
            logger.error(f"[WS] Ошибка обработки ответа: {e}", exc_info=True)
            await websocket.send_json({
                "type": "error",
                "message": "Ошибка при обработке ответа"
            })
    
    return handle_responses


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """
    WebSocket эндпоинт для чата.
    
    Flow:
    1. Принимаем сообщение
    2. Pre-модерация
    3. Отправляем запросы всем моделям
    4. Собираем ответы в Buffer
    5. Отправляем результат клиенту
    """
    await websocket.accept()
    client_id = id(websocket)
    logger.info(f"[WS] Клиент {client_id} подключился")
    
    try:
        while True:
            # Получаем сообщение
            user_message = await websocket.receive_text()
            request_id = f"req_{int(time.time())}_{client_id}"
            
            logger.info(f"[WS] Запрос #{request_id}: {user_message[:50]}...")
            
            # Pre-модерация
            moderated_input = await content_moderator.apply_pre_moderation(user_message)
            if not moderated_input:
                await websocket.send_json({
                    "type": "error",
                    "message": "Ваш запрос содержит недопустимый контент"
                })
                continue
            
            # Устанавливаем callback и контекст
            response_buffer.set_context(request_id, user_message)
            response_buffer.set_callback(
                request_id,
                create_response_handler(websocket, request_id, user_message)
            )
            
            # Отправляем запросы всем моделям
            context = {
                "user_message": user_message,
                "request_id": request_id
            }
            asyncio.create_task(model_registry.query_all(moderated_input, context))
            
    except WebSocketDisconnect:
        logger.info(f"[WS] Клиент {client_id} отключился")
    except Exception as e:
        logger.error(f"[WS] Ошибка WebSocket: {e}", exc_info=True)
    finally:
        try:
            await websocket.close()
        except:
            pass


# ============================================================================
# HTTP эндпоинты
# ============================================================================

@app.get("/")
async def home():
    """Корневой эндпоинт."""
    return {
        "message": "Добро пожаловать в 'Столик на троих' API",
        "version": "2.0.0",
        "docs": "/docs"
    }


@app.get("/health", response_model=HealthResponse)
async def health_check():
    """
    Health-check с расширенной статистикой.
    
    Returns:
        Статус системы и метрики по каждой модели
    """
    if not model_registry or not response_buffer:
        raise HTTPException(status_code=503, detail="Система не инициализирована")
    
    stats = response_buffer.get_stats()
    model_statuses = {}
    model_times = {}
    
    for model_id, adapter in model_registry.adapters.items():
        try:
            model_statuses[model_id] = await adapter.health_check()
            model_times[model_id] = adapter.last_response_time
        except Exception as e:
            logger.debug(f"[Health] Ошибка проверки {model_id}: {e}")
            model_statuses[model_id] = False
            model_times[model_id] = None
    
    return {
        "status": "healthy",
        "models_loaded": len(model_registry.adapters),
        "active_requests": stats["active_requests"],
        "success_rate": stats["success_rate"],
        "avg_response_time_global": stats["avg_response_time"],
        "model_statuses": model_statuses,
        "model_avg_response_time": model_times
    }


@app.post("/analyze-webchat")
async def analyze_webchat(request: WebChatAnalysisRequest):
    """
    Анализ веб-чата и генерация конфигурации.
    
    Args:
        request: URL и тестовый промпт
        
    Returns:
        Конфигурация для подключения к веб-чату
    """
    if not webchat_analyzer:
        raise HTTPException(status_code=503, detail="Анализатор не инициализирован")
    
    result = await webchat_analyzer.analyze_webchat(request.url, request.test_prompt)
    
    if result["success"]:
        return {
            "status": "success",
            "config": result["config"],
            "message": "Анализ завершен успешно"
        }
    else:
        raise HTTPException(status_code=400, detail=result["message"])


@app.get("/stats")
async def get_stats():
    """Получить статистику буфера."""
    if not response_buffer:
        raise HTTPException(status_code=503, detail="Буфер не инициализирован")
    
    return response_buffer.get_stats()


# ============================================================================
# Точка входа
# ============================================================================

if __name__ == "__main__":
    import uvicorn
    
    logger.info("🚀 Запуск сервера 'Столик на троих'...")
    uvicorn.run(
        "main:app",
        host=settings.host,
        port=settings.port,
        reload=False,
        log_level=settings.logging.level.lower()
    )
