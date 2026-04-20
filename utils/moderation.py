"""
Система модерации контента для чата.

Features:
- Pre-moderation: фильтрация входящих запросов
- Post-moderation: фильтрация ответов моделей
- Identity protection: скрытие признаков ИИ
- PII redaction: маскирование персональных данных
- Интеграция с OpenAI Moderation API
"""

import logging
import re
from typing import Dict, Any, Optional

import httpx

from config.settings import settings

logger = logging.getLogger("ContentModerator")


class ContentModerator:
    """Система модерации контента для чата."""

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        """
        Args:
            config: Конфигурация модерации. Если None, используются настройки из settings.
        """
        self.config = config or {}
        self._openai_api_key: Optional[str] = settings.moderation.openai_api_key
        
        # Загружаем настройки
        self._pre_enabled = settings.moderation.pre_moderation_enabled
        self._post_enabled = settings.moderation.post_moderation_enabled
        self._pii_enabled = settings.moderation.pii_redaction_enabled
        self._identity_enabled = settings.moderation.identity_protection_enabled
        self._threshold = settings.moderation.threshold
        self._block_keywords = [kw.lower() for kw in settings.moderation.block_keywords]
        
        logger.info(
            f"[Moderator] Инициализирован: pre={self._pre_enabled}, "
            f"post={self._post_enabled}, pii={self._pii_enabled}"
        )

    def set_openai_api_key(self, api_key: str) -> None:
        """Установить API ключ для OpenAI Moderation API."""
        self._openai_api_key = api_key
        logger.info("[Moderator] API ключ установлен")

    async def moderate_text(self, text: str) -> Dict[str, Any]:
        """
        Модерация текста через OpenAI Moderation API.
        
        Args:
            text: Текст для проверки
            
        Returns:
            Результат модерации в формате OpenAI
        """
        if not self._openai_api_key:
            return {"flagged": False, "categories": {}, "category_scores": {}}

        async with httpx.AsyncClient() as client:
            try:
                response = await client.post(
                    "https://api.openai.com/v1/moderations",
                    headers={"Authorization": f"Bearer {self._openai_api_key}"},
                    json={"input": text},
                    timeout=10.0
                )
                response.raise_for_status()
                result = response.json()
                return result["results"][0]
                
            except httpx.HTTPStatusError as e:
                logger.error(f"[Moderator] HTTP ошибка API: {e.response.status_code}")
                return {"flagged": False, "categories": {}, "category_scores": {}}
            except Exception as e:
                logger.error(f"[Moderator] Ошибка API: {e}")
                return {"flagged": False, "categories": {}, "category_scores": {}}

    async def apply_pre_moderation(self, user_input: str) -> Optional[str]:
        """
        Предварительная модерация пользовательского ввода.
        
        Args:
            user_input: Ввод пользователя
            
        Returns:
            Оригинальный текст если прошёл модерацию, None если заблокирован
        """
        if not self._pre_enabled:
            return user_input
            
        # Проверка длины
        if len(user_input) > settings.workflow.max_message_length:
            logger.warning(f"[Moderator] Сообщение слишком длинное: {len(user_input)}")
            return None

        # Проверка по ключевым словам
        input_lower = user_input.lower()
        for keyword in self._block_keywords:
            if keyword in input_lower:
                logger.warning(f"[Moderator] Блокировка по ключевому слову: {keyword}")
                return None

        # Проверка через OpenAI API
        if self._openai_api_key:
            try:
                moderation_result = await self.moderate_text(user_input)
                if moderation_result["flagged"] or any(
                    score > self._threshold 
                    for score in moderation_result.get("category_scores", {}).values()
                ):
                    logger.warning("[Moderator] Блокировка по OpenAI Moderation API")
                    return None
            except Exception as e:
                logger.error(f"[Moderator] Ошибка pre-модерации: {e}")
                # При ошибке API пропускаем (fail-open)
                
        return user_input

    async def apply_post_moderation(self, response: str) -> str:
        """
        Пост-модерация ответа модели.
        
        Args:
            response: Ответ модели
            
        Returns:
            Отфильтрованный ответ
        """
        if not self._post_enabled:
            return response

        # Проверка через OpenAI API
        if self._openai_api_key:
            try:
                moderation_result = await self.moderate_text(response)
                if moderation_result["flagged"] or any(
                    score > self._threshold
                    for score in moderation_result.get("category_scores", {}).values()
                ):
                    logger.warning("[Moderator] Post-модерация: контент заблокирован")
                    return "К сожалению, я не могу предоставить этот ответ."
            except Exception as e:
                logger.error(f"[Moderator] Ошибка post-модерации: {e}")
                
        return response

    def apply_identity_protection(
        self,
        response: str,
        custom_rules: Optional[list] = None
    ) -> str:
        """
        Применение правил скрытия природы ИИ.
        
        Args:
            response: Ответ модели
            custom_rules: Дополнительные правила (опционально)
            
        Returns:
            Ответ с примененными правилами
        """
        if not self._identity_enabled:
            return response

        # Применяем кастомные правила
        rules = custom_rules or []
        for rule in rules:
            pattern = rule.get("pattern")
            action = rule.get("action")
            replacement = rule.get("replacement", "")

            if pattern and action:
                if action == "replace" and replacement:
                    response = re.sub(pattern, replacement, response, flags=re.IGNORECASE)
                elif action == "remove":
                    response = re.sub(pattern, "", response, flags=re.IGNORECASE)

        # Удаление типичных фраз
        typical_phrases = [
            r"Я являюсь языковой моделью",
            r"Я искусственный интеллект",
            r"Я ИИ-ассистент",
            r"Я модель искусственного интеллекта",
            r"Я большая языковая модель",
            r"Я AI-ассистент",
            r"As an AI language model",
            r"I am an AI assistant",
        ]
        
        for phrase in typical_phrases:
            response = re.sub(phrase, "", response, flags=re.IGNORECASE)

        return response.strip()

    def redact_pii(self, text: str) -> str:
        """
        Редактирование персональных данных (PII).
        
        Args:
            text: Исходный текст
            
        Returns:
            Текст с отредактированными PII
        """
        if not self._pii_enabled:
            return text

        # Email
        email_pattern = r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b'
        text = re.sub(email_pattern, "[EMAIL REDACTED]", text)

        # Телефоны (разные форматы)
        phone_patterns = [
            r'\+?\d{1,3}[-.\s]?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}',  # +1 (123) 456-7890
            r'\b\d{3}[-.\s]?\d{3}[-.\s]?\d{4}\b',  # 123-456-7890
        ]
        for pattern in phone_patterns:
            text = re.sub(pattern, "[PHONE REDACTED]", text)

        # SSN (US)
        ssn_pattern = r'\b\d{3}[-.\s]?\d{2}[-.\s]?\d{4}\b'
        text = re.sub(ssn_pattern, "[SSN REDACTED]", text)

        return text

    async def process_response(
        self,
        response: str,
        custom_rules: Optional[list] = None
    ) -> str:
        """
        Полная обработка ответа: post-moderation + identity + PII.
        
        Args:
            response: Исходный ответ
            custom_rules: Дополнительные правила
            
        Returns:
            Полностью обработанный ответ
        """
        # Post-модерация
        moderated = await self.apply_post_moderation(response)
        
        # Защита идентичности
        protected = self.apply_identity_protection(moderated, custom_rules)
        
        # PII
        final = self.redact_pii(protected)
        
        return final
