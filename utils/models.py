"""
Адаптеры моделей и реестр с единым Playwright и пулом HTTP-клиентов.

Architecture:
- BaseModelAdapter: абстрактный базовый класс
- APIModelAdapter: HTTP API с пулом клиентов
- WebChatAdapter: Playwright-браузер (использует единый Playwright из Registry)
- ModelRegistry: управление всеми адаптерами, единый Playwright
"""

import asyncio
import logging
import time
from abc import ABC, abstractmethod
from typing import Dict, Any, Optional

import httpx
from playwright.async_api import async_playwright, Playwright, Browser

from config.settings import settings

logger = logging.getLogger("ModelRegistry")


class BaseModelAdapter(ABC):
    """Абстрактный базовый класс адаптера модели."""

    def __init__(self, model_id: str, config: Dict[str, Any]):
        """
        Args:
            model_id: Уникальный идентификатор модели
            config: Конфигурация модели из models.json
        """
        self.model_id = model_id
        self.config = config
        self.last_response_time: Optional[float] = None
        self._closed = False

    @abstractmethod
    async def query(self, prompt: str, context: Dict[str, Any]) -> str:
        """
        Отправить запрос к модели.
        
        Args:
            prompt: Текст запроса
            context: Дополнительный контекст
            
        Returns:
            Ответ модели
        """
        pass

    @abstractmethod
    async def health_check(self) -> bool:
        """Проверка доступности модели."""
        pass

    @abstractmethod
    async def close(self) -> None:
        """Закрыть адаптер и освободить ресурсы."""
        pass

    def is_closed(self) -> bool:
        """Проверить, закрыт ли адаптер."""
        return self._closed


class APIModelAdapter(BaseModelAdapter):
    """
    Адаптер для моделей, доступных через HTTP API.
    
    Features:
    - Пул HTTP-клиентов (один клиент на адаптер)
    - Автоматическое закрытие соединений
    - Таймауты и retry-логика
    """

    def __init__(self, model_id: str, config: Dict[str, Any]):
        super().__init__(model_id, config)
        self._client: Optional[httpx.AsyncClient] = None
        self._client_lock = asyncio.Lock()
        
        # Параметры из конфига
        self.endpoint = config.get("endpoint", "")
        self.health_endpoint = config.get("health_endpoint", "")
        self.api_key = config.get("api_key", "")
        self.model_name = config.get("name", model_id)
        self.timeout = config.get("timeout", 30.0)

    async def _get_client(self) -> httpx.AsyncClient:
        """Получить или создать HTTP-клиент (lazy initialization)."""
        if self._client is None or self._client.is_closed:
            async with self._client_lock:
                if self._client is None or self._client.is_closed:
                    self._client = httpx.AsyncClient(
                        timeout=self.timeout,
                        limits=httpx.Limits(max_keepalive_connections=5, max_connections=10)
                    )
                    logger.debug(f"[{self.model_id}] HTTP клиент создан")
        return self._client

    async def query(self, prompt: str, context: Dict[str, Any]) -> str:
        """Отправить запрос к API модели."""
        if self._closed:
            return "⚠️ Адаптер закрыт"
            
        start = time.time()
        try:
            client = await self._get_client()
            
            payload = {
                "model": self.model_name,
                "prompt": prompt,
                **context
            }
            
            headers = {}
            if self.api_key:
                headers["Authorization"] = f"Bearer {self.api_key}"
            
            response = await client.post(
                self.endpoint,
                headers=headers,
                json=payload
            )
            response.raise_for_status()
            data = response.json()
            
            # Извлекаем текст ответа (поддержка разных форматов)
            result = self._extract_response_text(data)
            
            self.last_response_time = time.time() - start
            logger.debug(f"[{self.model_id}] Ответ получен за {self.last_response_time:.2f}s")
            return result

        except httpx.TimeoutException:
            logger.error(f"[{self.model_id}] Таймаут запроса")
            return "⚠️ Таймаут при запросе к модели"
        except httpx.HTTPStatusError as e:
            logger.error(f"[{self.model_id}] HTTP ошибка: {e.response.status_code}")
            return f"⚠️ Ошибка API: {e.response.status_code}"
        except Exception as e:
            logger.error(f"[{self.model_id}] Ошибка запроса: {e}", exc_info=True)
            return "⚠️ Ошибка при запросе к модели"

    def _extract_response_text(self, data: Dict[str, Any]) -> str:
        """Извлечь текст ответа из различных форматов API."""
        # OpenAI-like format
        if "choices" in data:
            return data.get("choices", [{}])[0].get("text", "").strip()
        # Simple format
        if "response" in data:
            return data.get("response", "").strip()
        if "content" in data:
            return data.get("content", "").strip()
        # Fallback
        return str(data)

    async def health_check(self) -> bool:
        """Проверить доступность API."""
        if not self.health_endpoint:
            # Если нет health endpoint, считаем модель доступной
            return True
            
        try:
            client = await self._get_client()
            response = await client.get(self.health_endpoint)
            return response.status_code == 200
        except Exception as e:
            logger.debug(f"[{self.model_id}] Health check failed: {e}")
            return False

    async def close(self) -> None:
        """Закрыть HTTP-клиент."""
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            logger.debug(f"[{self.model_id}] HTTP клиент закрыт")
        self._closed = True


class WebChatAdapter(BaseModelAdapter):
    """
    Адаптер для веб-чатов через Playwright.
    
    Features:
    - Использует единый Playwright из ModelRegistry
    - Поддержка API-эмуляции (приоритет) и браузерной автоматизации
    - Автоматическое управление страницами
    """

    def __init__(
        self,
        model_id: str,
        config: Dict[str, Any],
        playwright: Optional[Playwright] = None,
        browser: Optional[Browser] = None
    ):
        """
        Args:
            model_id: ID модели
            config: Конфигурация
            playwright: Единый экземпляр Playwright (из ModelRegistry)
            browser: Единый экземпляр Browser (из ModelRegistry)
        """
        super().__init__(model_id, config)
        self._playwright = playwright
        self._browser = browser
        self._owned_browser = False  # Флаг: мы создали browser сами?
        
        # Конфигурация
        self.endpoint = config.get("endpoint")  # API-эмуляция (опционально)
        self.url = config.get("url")  # URL для Playwright
        self.selectors = config.get("selectors", {})
        self.request_template = config.get("request_template", {})
        self.health_endpoint = config.get("health_endpoint")

    async def query(self, prompt: str, context: Dict[str, Any]) -> str:
        """Отправить запрос через API или Playwright."""
        if self._closed:
            return "⚠️ Адаптер закрыт"
            
        start = time.time()
        
        # Приоритет: API-эмуляция (быстрее)
        if self.endpoint:
            return await self._query_api(prompt, context, start)
        
        # Fallback: Playwright
        if self.url:
            return await self._query_playwright(prompt, context, start)
        
        return "⚠️ Не настроен endpoint или url"

    async def _query_api(
        self,
        prompt: str,
        context: Dict[str, Any],
        start_time: float
    ) -> str:
        """Запрос через API-эмуляцию."""
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                payload = dict(self.request_template)
                payload.update({"prompt": prompt, **context})
                
                response = await client.post(self.endpoint, json=payload)
                response.raise_for_status()
                data = response.json()
                
                result = data.get("response", "").strip()
                self.last_response_time = time.time() - start_time
                return result
                
        except Exception as e:
            logger.error(f"[{self.model_id}] Ошибка API: {e}")
            return "⚠️ Ошибка при работе с веб-чатом (API)"

    async def _query_playwright(
        self,
        prompt: str,
        context: Dict[str, Any],
        start_time: float
    ) -> str:
        """Запрос через Playwright (браузер)."""
        if not self._browser:
            return "⚠️ Браузер не инициализирован"
            
        try:
            page = await self._browser.new_page()
            
            try:
                await page.goto(self.url, timeout=settings.playwright_timeout)
                
                # Ввод запроса
                input_selector = self.selectors.get("input", "input[type=text], textarea")
                await page.fill(input_selector, prompt)
                
                # Отправка
                submit_selector = self.selectors.get("submit", "button[type=submit], button")
                await page.click(submit_selector)
                
                # Ожидание ответа
                output_selector = self.selectors.get("output", "div, p")
                await page.wait_for_selector(output_selector, timeout=settings.playwright_timeout)
                
                result = await page.inner_text(output_selector)
                self.last_response_time = time.time() - start_time
                return result.strip()
                
            finally:
                await page.close()
                
        except Exception as e:
            logger.error(f"[{self.model_id}] Ошибка Playwright: {e}", exc_info=True)
            return "⚠️ Ошибка при работе с веб-чатом (Browser)"

    async def health_check(self) -> bool:
        """Проверить доступность веб-чата."""
        try:
            if self.health_endpoint:
                async with httpx.AsyncClient(timeout=10.0) as client:
                    response = await client.get(self.health_endpoint)
                    return response.status_code == 200
                    
            elif self.url and self._browser:
                page = await self._browser.new_page()
                try:
                    await page.goto(self.url, timeout=10000)
                    return True
                finally:
                    await page.close()
                    
            return bool(self.endpoint)  # Если есть endpoint, считаем доступным
            
        except Exception as e:
            logger.debug(f"[{self.model_id}] Health check failed: {e}")
            return False

    async def close(self) -> None:
        """Закрыть адаптер."""
        # Не закрываем browser/playwright - они общие!
        self._closed = True


class ModelRegistry:
    """
    Реестр всех моделей с единым Playwright.
    
    Features:
    - Единый Playwright и Browser для всех WebChatAdapter
    - Пул HTTP-клиентов для APIModelAdapter
    - Graceful shutdown
    """

    def __init__(self, models_config: Dict[str, Any]):
        """
        Args:
            models_config: Конфигурация моделей из models.json
        """
        self.models_config = models_config
        self.adapters: Dict[str, BaseModelAdapter] = {}
        self._response_buffer = None
        
        # Единые экземпляры Playwright (инициализируются лениво)
        self._playwright: Optional[Playwright] = None
        self._browser: Optional[Browser] = None
        self._initialized = False

    async def initialize(self) -> None:
        """
        Инициализация реестра.
        Создаёт единый Playwright для WebChat-адаптеров.
        """
        if self._initialized:
            return
            
        logger.info("[Registry] Инициализация реестра моделей...")
        
        # Проверяем, нужен ли Playwright
        need_playwright = any(
            cfg.get("type") == "webchat" and "endpoint" not in cfg
            for cfg in self.models_config.get("models", [])
        )
        
        if need_playwright:
            logger.info("[Registry] Запуск Playwright...")
            self._playwright = await async_playwright().start()
            self._browser = await self._playwright.chromium.launch(
                headless=settings.playwright_headless
            )
            logger.info("[Registry] Playwright запущен")
        
        self._initialized = True
        logger.info(f"[Registry] Реестр инициализирован, моделей: {len(self.adapters)}")

    def attach_buffer(self, buffer) -> None:
        """
        Привязать ResponseBuffer и создать адаптеры.
        
        Args:
            buffer: Экземпляр ResponseBuffer
        """
        self._response_buffer = buffer
        
        for cfg in self.models_config.get("models", []):
            model_id = cfg["id"]
            model_type = cfg.get("type", "api")
            
            try:
                if model_type == "api":
                    adapter = APIModelAdapter(model_id, cfg)
                    logger.info(f"[Registry] Создан API адаптер: {model_id}")
                    
                elif model_type == "webchat":
                    adapter = WebChatAdapter(
                        model_id, cfg,
                        playwright=self._playwright,
                        browser=self._browser
                    )
                    logger.info(f"[Registry] Создан WebChat адаптер: {model_id}")
                    
                else:
                    logger.warning(f"[Registry] Неизвестный тип модели: {model_type}")
                    continue
                    
                self.adapters[model_id] = adapter
                
            except Exception as e:
                logger.error(f"[Registry] Ошибка создания адаптера {model_id}: {e}")

    async def query_all(self, prompt: str, context: Dict[str, Any]) -> None:
        """
        Отправить запрос ко всем моделям параллельно.
        
        Args:
            prompt: Текст запроса
            context: Контекст (должен содержать request_id!)
        """
        if not self._initialized:
            await self.initialize()
            
        tasks = []
        for model_id, adapter in self.adapters.items():
            if not adapter.is_closed():
                tasks.append(self._safe_query(adapter, model_id, prompt, context))
            else:
                logger.warning(f"[Registry] Адаптер {model_id} закрыт, пропускаем")
        
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _safe_query(
        self,
        adapter: BaseModelAdapter,
        model_id: str,
        prompt: str,
        context: Dict[str, Any]
    ) -> None:
        """Безопасный запрос к модели с обработкой ошибок."""
        try:
            result = await adapter.query(prompt, context)
            
            if self._response_buffer:
                request_id = context.get("request_id")
                if request_id:
                    await self._response_buffer.add_response(request_id, model_id, result)
                else:
                    logger.error(f"[Registry] request_id отсутствует в context!")
                    
        except Exception as e:
            logger.error(f"[Registry] Ошибка запроса к {model_id}: {e}", exc_info=True)
            # Отправляем ошибку как ответ
            if self._response_buffer:
                request_id = context.get("request_id")
                if request_id:
                    await self._response_buffer.add_response(
                        request_id, model_id, f"⚠️ Ошибка: {str(e)}"
                    )

    async def close_all(self) -> None:
        """Graceful shutdown: закрыть все адаптеры и Playwright."""
        logger.info("[Registry] Начинаем graceful shutdown...")
        
        # Закрываем все адаптеры
        close_tasks = []
        for model_id, adapter in self.adapters.items():
            if not adapter.is_closed():
                close_tasks.append(adapter.close())
        
        if close_tasks:
            await asyncio.gather(*close_tasks, return_exceptions=True)
        
        self.adapters.clear()
        
        # Закрываем единый Browser
        if self._browser:
            await self._browser.close()
            self._browser = None
            logger.info("[Registry] Browser закрыт")
        
        # Закрываем единый Playwright
        if self._playwright:
            await self._playwright.stop()
            self._playwright = None
            logger.info("[Registry] Playwright остановлен")
        
        self._initialized = False
        logger.info("[Registry] Graceful shutdown завершён")
