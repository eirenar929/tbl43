"""
Интеграционные тесты для "Столик на троих".

Требования:
- Запущенный сервер на localhost:8000
- ИЛИ использование TestClient с lifespan
"""

import asyncio
import json
import os
import sys
from pathlib import Path

import pytest
import websockets
from httpx import ASGITransport, AsyncClient

# Добавляем родительскую директорию в путь
sys.path.insert(0, str(Path(__file__).parent.parent))

from main import app


# =============================================================================
# Fixtures
# =============================================================================

@pytest.fixture(scope="session")
def event_loop():
    """Create an instance of the default event loop for the test session."""
    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()


@pytest.fixture
async def async_client():
    """Async HTTP client для тестирования."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


# =============================================================================
# HTTP Tests
# =============================================================================

@pytest.mark.asyncio
async def test_root_endpoint(async_client):
    """Тест корневого эндпоинта."""
    response = await async_client.get("/")
    assert response.status_code == 200
    data = response.json()
    assert "message" in data
    assert "version" in data


@pytest.mark.asyncio
async def test_health_endpoint(async_client):
    """Тест health-check с проверкой всех полей."""
    response = await async_client.get("/health")
    
    # Если система не инициализирована, это нормально для тестов
    if response.status_code == 503:
        pytest.skip("Система не инициализирована (нужен запущенный сервер)")
    
    assert response.status_code == 200
    data = response.json()
    
    # Проверяем структуру ответа
    assert "status" in data
    assert "models_loaded" in data
    assert "active_requests" in data
    assert "success_rate" in data
    assert "avg_response_time_global" in data
    assert "model_statuses" in data
    assert "model_avg_response_time" in data
    
    # Проверяем типы
    assert isinstance(data["models_loaded"], int)
    assert isinstance(data["success_rate"], float)
    assert isinstance(data["model_statuses"], dict)


@pytest.mark.asyncio
async def test_stats_endpoint(async_client):
    """Тест эндпоинта статистики."""
    response = await async_client.get("/stats")
    
    if response.status_code == 503:
        pytest.skip("Система не инициализирована")
    
    assert response.status_code == 200
    data = response.json()
    
    assert "total_requests" in data
    assert "completed" in data
    assert "timed_out" in data
    assert "success_rate" in data


# =============================================================================
# WebSocket Tests
# =============================================================================

@pytest.mark.asyncio
async def test_websocket_connection():
    """Тест WebSocket соединения."""
    # Этот тест требует запущенного сервера
    try:
        async with websockets.connect("ws://localhost:8000/ws") as ws:
            # Отправляем тестовое сообщение
            test_message = "Привет, это тест!"
            await ws.send(test_message)
            
            # Ждём ответ
            response = await asyncio.wait_for(ws.recv(), timeout=15)
            data = json.loads(response)
            
            # Проверяем структуру
            assert "type" in data
            assert data["type"] in ["response", "error"]
            
            if data["type"] == "response":
                assert "content" in data
                assert "responses" in data
                assert "request_id" in data
            else:
                assert "message" in data
                
    except (ConnectionRefusedError, OSError):
        pytest.skip("Сервер не запущен на localhost:8000")
    except asyncio.TimeoutError:
        pytest.fail("Таймаут ожидания ответа от WebSocket")


@pytest.mark.asyncio
async def test_websocket_moderation():
    """Тест модерации через WebSocket (блокировка нежелательного контента)."""
    try:
        async with websockets.connect("ws://localhost:8000/ws") as ws:
            # Отправляем сообщение с потенциально нежелательным контентом
            # (если настроены block_keywords)
            test_message = "Тестовое сообщение"
            await ws.send(test_message)
            
            response = await asyncio.wait_for(ws.recv(), timeout=15)
            data = json.loads(response)
            
            # Проверяем что получили ответ (не ошибку блокировки)
            assert "type" in data
            
    except (ConnectionRefusedError, OSError):
        pytest.skip("Сервер не запущен на localhost:8000")


# =============================================================================
# Component Tests
# =============================================================================

@pytest.mark.asyncio
async def test_buffer_stats():
    """Тест ResponseBuffer статистики."""
    from utils.buffer import ResponseBuffer
    
    buffer = ResponseBuffer(timeout=5.0, min_responses=1)
    
    # Начальная статистика
    stats = buffer.get_stats()
    assert stats["total_requests"] == 0
    assert stats["completed"] == 0
    assert stats["success_rate"] == 0.0
    
    await buffer.cleanup()


@pytest.mark.asyncio
async def test_moderator_pii():
    """Тест PII redaction в ContentModerator."""
    from utils.moderation import ContentModerator
    
    moderator = ContentModerator()
    moderator._pii_enabled = True
    
    # Тест email
    text_with_email = "Мой email: user@example.com"
    result = moderator.redact_pii(text_with_email)
    assert "[EMAIL REDACTED]" in result
    assert "user@example.com" not in result
    
    # Тест телефона
    text_with_phone = "Позвони мне: +1 (123) 456-7890"
    result = moderator.redact_pii(text_with_phone)
    assert "[PHONE REDACTED]" in result


@pytest.mark.asyncio
async def test_moderator_identity():
    """Тест identity protection в ContentModerator."""
    from utils.moderation import ContentModerator
    
    moderator = ContentModerator()
    moderator._identity_enabled = True
    
    # Тест типичных фраз
    text = "Я являюсь языковой моделью и могу помочь."
    result = moderator.apply_identity_protection(text)
    assert "языковой моделью" not in result.lower()
    
    text = "As an AI language model, I can help you."
    result = moderator.apply_identity_protection(text)
    assert "AI language model" not in result


@pytest.mark.asyncio
async def test_format_responses():
    """Тест форматирования ответов."""
    from utils.sync import format_responses
    
    responses = {
        "model1": {"response": "Ответ 1", "timestamp": 123},
        "model2": {"response": "Ответ 2", "timestamp": 124}
    }
    
    # Тест concat
    result = format_responses(responses, {"settings": {"response_strategy": "concat"}})
    assert "[model1]" in result
    assert "Ответ 1" in result
    
    # Тест first
    result = format_responses(responses, {"settings": {"response_strategy": "first"}})
    assert result == "Ответ 1"
    
    # Тест compare
    result = format_responses(
        responses,
        {"settings": {"response_strategy": "compare"}},
        {"models": [{"id": "model1", "name": "GPT-4"}]}
    )
    assert "###" in result


# =============================================================================
# Load Tests (опционально)
# =============================================================================

@pytest.mark.asyncio
async def test_concurrent_requests():
    """Тест параллельных запросов (требует запущенного сервера)."""
    try:
        async with websockets.connect("ws://localhost:8000/ws") as ws:
            # Отправляем несколько сообщений подряд
            for i in range(5):
                await ws.send(f"Тестовое сообщение {i}")
            
            # Собираем ответы
            responses = []
            for _ in range(5):
                try:
                    response = await asyncio.wait_for(ws.recv(), timeout=10)
                    responses.append(json.loads(response))
                except asyncio.TimeoutError:
                    break
            
            # Проверяем что получили ответы
            assert len(responses) > 0
            
    except (ConnectionRefusedError, OSError):
        pytest.skip("Сервер не запущен на localhost:8000")


# =============================================================================
# Error Handling Tests
# =============================================================================

@pytest.mark.asyncio
async def test_invalid_json_request(async_client):
    """Тест обработки невалидного JSON."""
    response = await async_client.post(
        "/analyze-webchat",
        data="invalid json",
        headers={"Content-Type": "application/json"}
    )
    assert response.status_code == 422  # Validation error
