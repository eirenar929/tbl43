"""
Синхронизация и форматирование ответов от нескольких моделей.

Features:
- ModerationHeuristics: эвристическая проверка необходимости модерации
- format_responses: форматирование ответов по разным стратегиям
"""

import re
from typing import Dict, Any, Optional

from config.settings import settings


class ModerationHeuristics:
    """
    Эвристическая проверка: нужно ли отправлять текст на модерацию.
    
    Используется для быстрой предварительной проверки перед дорогой
    модерацией через API.
    """

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        """
        Args:
            config: Конфигурация эвристик
        """
        self.config = config or {}
        self.enabled = self.config.get("enabled", True)
        self.min_length = self.config.get("min_length", 0)
        self.max_length = self.config.get("max_length", 2000)
        self.block_keywords = [
            w.lower() for w in self.config.get("block_keywords", [])
        ]
        self.allow_keywords = [
            w.lower() for w in self.config.get("allow_keywords", [])
        ]
        self.block_urls = self.config.get("block_urls", True)
        self.block_patterns = self.config.get("block_patterns", [])

    def needs_moderation(self, text: str) -> bool:
        """
        Определяет, нужен ли тексту прогон через полную модерацию.
        
        Args:
            text: Текст для проверки
            
        Returns:
            True если нужна полная модерация
        """
        if not self.enabled:
            return False

        text_lower = text.lower()

        # 1. Проверка длины
        if len(text) < self.min_length:
            return True
        if len(text) > self.max_length:
            return True

        # 2. Проверка запрещённых ключевых слов
        for kw in self.block_keywords:
            if kw in text_lower:
                return True

        # 3. Проверка "разрешённых" слов (whitelist)
        if self.allow_keywords:
            if not any(kw in text_lower for kw in self.allow_keywords):
                return True

        # 4. Проверка наличия URL
        if self.block_urls and re.search(r"https?://\S+", text):
            return True

        # 5. Проверка кастомных паттернов
        for pattern in self.block_patterns:
            if re.search(pattern, text, re.IGNORECASE):
                return True

        return False

    def check_safety_score(self, text: str) -> float:
        """
        Вычислить оценку "безопасности" текста (0.0 - 1.0).
        
        Args:
            text: Текст для оценки
            
        Returns:
            Оценка безопасности (1.0 = полностью безопасен)
        """
        score = 1.0
        text_lower = text.lower()
        
        # Штраф за длину
        if len(text) > 1000:
            score -= 0.1
        if len(text) > 2000:
            score -= 0.2
            
        # Штраф за ключевые слова
        for kw in self.block_keywords:
            if kw in text_lower:
                score -= 0.3
                
        # Штраф за URL
        if self.block_urls and re.search(r"https?://\S+", text):
            score -= 0.2
            
        return max(0.0, score)


def format_responses(
    responses: Dict[str, Dict[str, Any]],
    workflow_config: Optional[Dict[str, Any]] = None,
    models_config: Optional[Dict[str, Any]] = None
) -> str:
    """
    Форматирование собранных ответов в единый вывод.
    
    Args:
        responses: Словарь {model_id: {response, timestamp}}
        workflow_config: Конфигурация workflow
        models_config: Конфигурация моделей
        
    Returns:
        Отформатированный текст
    """
    if not responses:
        return "⚠️ Ответы от моделей не получены."

    strategy = settings.workflow.response_strategy
    if workflow_config:
        strategy = workflow_config.get("settings", {}).get("response_strategy", strategy)

    if strategy == "concat":
        return _format_concat(responses)
    elif strategy == "first":
        return _format_first(responses)
    elif strategy == "compare":
        return _format_compare(responses, models_config)
    elif strategy == "best":
        return _format_best(responses, models_config)
    else:
        return str(responses)


def _format_concat(responses: Dict[str, Dict[str, Any]]) -> str:
    """Конкатенация всех ответов."""
    parts = []
    for model_id, data in responses.items():
        response_text = data.get("response", "")
        parts.append(f"**[{model_id}]**: {response_text}")
    return "\n\n".join(parts)


def _format_first(responses: Dict[str, Dict[str, Any]]) -> str:
    """Только первый ответ (по порядку)."""
    first_model = next(iter(responses))
    return responses[first_model].get("response", "")


def _format_compare(
    responses: Dict[str, Dict[str, Any]],
    models_config: Optional[Dict[str, Any]]
) -> str:
    """Сравнительный формат с разделителями."""
    formatted = []
    
    # Строим маппинг id -> name
    name_map = {}
    if models_config:
        for m in models_config.get("models", []):
            name_map[m["id"]] = m.get("name", m["id"])
    
    for model_id, data in responses.items():
        model_name = name_map.get(model_id, model_id)
        response_text = data.get("response", "")
        formatted.append(f"### {model_name}\n{response_text}")
    
    return "\n\n---\n\n".join(formatted)


def _format_best(
    responses: Dict[str, Dict[str, Any]],
    models_config: Optional[Dict[str, Any]]
) -> str:
    """
    Выбор "лучшего" ответа (по длине и качеству).
    
    Эвристика: предпочитаем более длинные ответы без ошибок.
    """
    candidates = []
    
    for model_id, data in responses.items():
        text = data.get("response", "")
        
        # Пропускаем ошибки
        if text.startswith("⚠️"):
            continue
            
        # Оценка качества
        score = len(text)
        if "ошибка" in text.lower():
            score -= 100
            
        candidates.append((model_id, text, score))
    
    if not candidates:
        return "⚠️ Нет валидных ответов от моделей."
    
    # Сортируем по score
    candidates.sort(key=lambda x: x[2], reverse=True)
    return candidates[0][1]


def merge_responses(
    responses: Dict[str, Dict[str, Any]],
    separator: str = "\n\n"
) -> str:
    """
    Простое объединение ответов без форматирования.
    
    Args:
        responses: Ответы моделей
        separator: Разделитель между ответами
        
    Returns:
        Объединённый текст
    """
    texts = [
        data.get("response", "")
        for data in responses.values()
    ]
    return separator.join(texts)
