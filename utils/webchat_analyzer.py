"""
Анализатор веб-чатов для автоматического построения конфигурации.

Features:
- Анализ страницы через Playwright
- Автоопределение селекторов (заглушка для MVP)
- Единый Playwright с ModelRegistry
"""

import logging
from typing import Dict, Any, Optional

from playwright.async_api import Playwright, Browser, async_playwright

from config.settings import settings

logger = logging.getLogger("WebChatAnalyzer")


class WebChatAnalyzer:
    """
    Анализатор веб-чатов для автоматического построения request_template.
    
    Использует единый Playwright извне или создаёт свой при необходимости.
    """

    def __init__(
        self,
        playwright: Optional[Playwright] = None,
        browser: Optional[Browser] = None
    ):
        """
        Args:
            playwright: Внешний Playwright (опционально)
            browser: Внешний Browser (опционально)
        """
        self._playwright = playwright
        self._browser = browser
        self._owned_playwright = False  # Мы создали Playwright сами?
        self._owned_browser = False     # Мы создали Browser сами?

    async def _ensure_browser(self) -> Browser:
        """
        Ленивая инициализация Playwright/Browser.
        
        Returns:
            Экземпляр Browser
        """
        # Если нам передали готовый browser - используем его
        if self._browser:
            return self._browser
            
        # Если есть playwright но нет browser - создаём browser
        if self._playwright and not self._browser:
            self._browser = await self._playwright.chromium.launch(
                headless=settings.playwright_headless
            )
            self._owned_browser = True
            logger.debug("[Analyzer] Browser создан")
            return self._browser
            
        # Создаём всё сами
        if not self._playwright:
            self._playwright = await async_playwright().start()
            self._owned_playwright = True
            logger.debug("[Analyzer] Playwright запущен")
            
        if not self._browser:
            self._browser = await self._playwright.chromium.launch(
                headless=settings.playwright_headless
            )
            self._owned_browser = True
            logger.debug("[Analyzer] Browser создан")
            
        return self._browser

    async def analyze_webchat(
        self,
        url: str,
        test_prompt: str = "Тестовый запрос"
    ) -> Dict[str, Any]:
        """
        Анализирует веб-чат по URL и возвращает конфиг модели.
        
        Args:
            url: Адрес веб-чата
            test_prompt: Пробная фраза для теста
            
        Returns:
            Dict с ключами: success, config, message
        """
        page = None
        try:
            browser = await self._ensure_browser()
            page = await browser.new_page()
            
            logger.info(f"[Analyzer] Анализ {url}...")
            await page.goto(url, timeout=settings.playwright_timeout)
            
            # TODO: Реализовать "умный" парсинг селекторов
            # Пока возвращаем заглушку с универсальными селекторами
            config = {
                "id": f"webchat_{abs(hash(url)) % 10000:04d}",
                "type": "webchat",
                "name": f"WebChat {url.split('/')[-1] or 'Bot'}",
                "url": url,
                "selectors": {
                    "input": "input[type=text], textarea, [contenteditable]",
                    "submit": "button[type=submit], button:has-text('Send'), button:has-text('Отправить')",
                    "output": ".response, .message, .reply, div[role='log']"
                },
                "request_template": {
                    "prompt": test_prompt,
                    "stream": False
                }
            }
            
            logger.info(f"[Analyzer] Анализ {url} завершён успешно")
            return {"success": True, "config": config}

        except Exception as e:
            logger.error(f"[Analyzer] Ошибка анализа {url}: {e}", exc_info=True)
            return {
                "success": False,
                "message": f"Ошибка анализа вебчата: {str(e)}"
            }
        finally:
            if page:
                await page.close()

    async def close(self) -> None:
        """
        Graceful shutdown: закрыть Browser/Playwright если мы их создали.
        
        Важно: если Playwright/Browser были переданы извне (например, из ModelRegistry),
        мы их НЕ закрываем!
        """
        logger.info("[Analyzer] Закрытие анализатора...")
        
        if self._owned_browser and self._browser:
            await self._browser.close()
            self._browser = None
            logger.debug("[Analyzer] Browser закрыт")
            
        if self._owned_playwright and self._playwright:
            await self._playwright.stop()
            self._playwright = None
            logger.debug("[Analyzer] Playwright остановлен")
            
        logger.info("[Analyzer] Закрыт")
