"""
Асинхронный буфер для сбора и синхронизации ответов от нескольких моделей.
Включает JSON-логирование всех диалогов.
"""

import asyncio
import json
import logging
import time
from pathlib import Path
from typing import Dict, Any, Callable, Optional, Awaitable

from config.settings import settings

logger = logging.getLogger("ResponseBuffer")


class ResponseBuffer:
    """
    Асинхронный буфер для сбора ответов от нескольких ИИ-моделей.
    
    Features:
    - Асинхронная синхронизация через asyncio.Lock
    - Таймауты через asyncio.create_task
    - JSON-логирование всех диалогов
    - Статистика производительности
    """

    def __init__(
        self,
        timeout: float = 8.0,
        min_responses: int = 1,
        dialog_log_path: Optional[Path] = None
    ):
        """
        Args:
            timeout: Максимальное время ожидания ответов (секунды)
            min_responses: Минимальное количество ответов для публикации
            dialog_log_path: Путь к файлу логов диалогов
        """
        self.timeout = timeout
        self.min_responses = min_responses
        self.dialog_log_path = dialog_log_path or settings.logging.dialog_log_path
        
        # Буферы и синхронизация
        self._buffer: Dict[str, Dict[str, Any]] = {}
        self._callbacks: Dict[str, Callable[[Dict[str, Any]], Awaitable[None]]] = {}
        self._lock = asyncio.Lock()
        
        # Мониторинг
        self._monitoring = {
            "total_requests": 0,
            "completed": 0,
            "timed_out": 0,
            "avg_response_time": 0.0
        }
        
        # Убедимся что директория для логов существует
        if settings.logging.enable_dialog_logging:
            self.dialog_log_path.parent.mkdir(parents=True, exist_ok=True)

    async def add_response(self, request_id: str, model_id: str, response: str) -> None:
        """
        Добавление ответа модели в буфер.
        
        Args:
            request_id: Уникальный ID запроса
            model_id: ID модели
            response: Текст ответа
        """
        async with self._lock:
            if request_id not in self._buffer:
                self._buffer[request_id] = {
                    "responses": {},
                    "start_time": time.time(),
                    "user_message": None  # Будет установлено через set_context
                }
                self._monitoring["total_requests"] += 1
                
                # Запускаем таймер таймаута
                asyncio.create_task(self._start_timeout(request_id))
                logger.debug(f"[Buffer] Новый запрос #{request_id}")

            self._buffer[request_id]["responses"][model_id] = {
                "response": response,
                "timestamp": time.time()
            }
            
            logger.debug(f"[Buffer] Добавлен ответ от {model_id} для #{request_id}")
            await self._check_completion(request_id)

    def set_context(self, request_id: str, user_message: str) -> None:
        """
        Установить контекст запроса (исходное сообщение пользователя).
        
        Args:
            request_id: ID запроса
            user_message: Сообщение пользователя
        """
        if request_id in self._buffer:
            self._buffer[request_id]["user_message"] = user_message

    def set_callback(
        self,
        request_id: str,
        callback: Callable[[Dict[str, Any]], Awaitable[None]]
    ) -> None:
        """
        Установка callback для обработки собранных ответов.
        
        Args:
            request_id: ID запроса
            callback: Асинхронная функция-обработчик
        """
        self._callbacks[request_id] = callback
        logger.debug(f"[Buffer] Установлен callback для #{request_id}")

    async def _check_completion(self, request_id: str) -> None:
        """Проверка, собрали ли достаточно ответов."""
        if request_id not in self._buffer:
            return
            
        data = self._buffer[request_id]
        if len(data["responses"]) >= self.min_responses:
            await self._publish_results(request_id)

    async def _start_timeout(self, request_id: str) -> None:
        """Асинхронный таймер ожидания."""
        await asyncio.sleep(self.timeout)
        
        async with self._lock:
            if request_id in self._buffer:
                self._monitoring["timed_out"] += 1
                logger.warning(f"[Buffer] Таймаут для запроса #{request_id}")
                await self._publish_results(request_id)

    async def _publish_results(self, request_id: str) -> None:
        """
        Публикация результатов и вызов callback.
        Также выполняет JSON-логирование диалога.
        """
        if request_id not in self._buffer:
            return

        data = self._buffer.pop(request_id)
        responses = data["responses"]
        start_time = data["start_time"]
        user_message = data.get("user_message", "")

        if responses:
            elapsed = time.time() - start_time
            
            # Обновляем статистику
            total = self._monitoring["completed"] * self._monitoring["avg_response_time"]
            total += elapsed
            self._monitoring["completed"] += 1
            self._monitoring["avg_response_time"] = total / self._monitoring["completed"]
            
            logger.info(
                f"[Buffer] Запрос #{request_id} завершён за {elapsed:.2f}s, "
                f"получено ответов: {len(responses)}"
            )
            
            # JSON-логирование диалога
            if settings.logging.enable_dialog_logging:
                await self._log_dialog(request_id, user_message, responses, elapsed)

            # Вызываем callback
            callback = self._callbacks.pop(request_id, None)
            if callback:
                asyncio.create_task(callback(responses))
        else:
            logger.warning(f"[Buffer] Запрос #{request_id} без ответов")

    async def _log_dialog(
        self,
        request_id: str,
        user_message: str,
        responses: Dict[str, Any],
        elapsed: float
    ) -> None:
        """
        Логирование диалога в JSONL формате.
        
        Args:
            request_id: ID запроса
            user_message: Сообщение пользователя
            responses: Ответы моделей
            elapsed: Время выполнения
        """
        try:
            log_entry = {
                "timestamp": time.time(),
                "request_id": request_id,
                "user_message": user_message,
                "responses": {
                    model_id: resp_data["response"]
                    for model_id, resp_data in responses.items()
                },
                "response_times": {
                    model_id: resp_data["timestamp"]
                    for model_id, resp_data in responses.items()
                },
                "elapsed_seconds": round(elapsed, 3),
                "models_count": len(responses)
            }
            
            # Асинхронная запись через executor (чтобы не блокировать event loop)
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(
                None,
                self._write_log_sync,
                log_entry
            )
        except Exception as e:
            logger.error(f"[Buffer] Ошибка логирования диалога: {e}")

    def _write_log_sync(self, log_entry: Dict[str, Any]) -> None:
        """Синхронная запись лога (вызывается в executor)."""
        with open(self.dialog_log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(log_entry, ensure_ascii=False) + "\n")

    def get_stats(self) -> Dict[str, Any]:
        """
        Получение статистики по обработке запросов.
        
        Returns:
            Dict с полями: total_requests, completed, timed_out, 
                          avg_response_time, success_rate, active_requests
        """
        success_rate = (
            self._monitoring["completed"] / max(self._monitoring["total_requests"], 1)
        )
        return {
            **self._monitoring,
            "success_rate": round(success_rate, 4),
            "active_requests": len(self._buffer)
        }

    async def cleanup(self) -> None:
        """Очистка ресурсов при завершении работы."""
        logger.info(f"[Buffer] Cleanup: {len(self._buffer)} активных запросов")
        # Можно добавить обработку незавершённых запросов
