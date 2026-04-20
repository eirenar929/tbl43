"""
Pydantic Settings для валидации и управления конфигурацией проекта.
Все параметры могут быть переопределены через переменные окружения.
"""

import os
from pathlib import Path
from typing import List, Optional

from pydantic import Field, validator
from pydantic_settings import BaseSettings


class ModerationSettings(BaseSettings):
    """Настройки модерации контента"""
    enabled: bool = Field(default=True, description="Включить модерацию")
    pre_moderation_enabled: bool = Field(default=True, description="Предварительная модерация")
    post_moderation_enabled: bool = Field(default=True, description="Пост-модерация")
    pii_redaction_enabled: bool = Field(default=True, description="Маскирование PII")
    identity_protection_enabled: bool = Field(default=True, description="Защита идентичности ИИ")
    openai_api_key: Optional[str] = Field(default=None, description="API ключ OpenAI для модерации")
    threshold: float = Field(default=0.7, ge=0.0, le=1.0, description="Порог чувствительности модерации")
    block_keywords: List[str] = Field(default_factory=list, description="Запрещённые ключевые слова")
    
    class Config:
        env_prefix = "MOD_"


class WorkflowSettings(BaseSettings):
    """Настройки рабочего процесса"""
    response_timeout: float = Field(default=8.0, gt=0, description="Таймаут ожидания ответов (сек)")
    min_responses: int = Field(default=1, ge=1, description="Минимальное количество ответов")
    response_strategy: str = Field(default="compare", description="Стратегия форматирования: concat/first/compare")
    max_message_length: int = Field(default=2000, ge=1, description="Максимальная длина сообщения")
    
    @validator("response_strategy")
    def validate_strategy(cls, v):
        allowed = {"concat", "first", "compare"}
        if v not in allowed:
            raise ValueError(f"strategy must be one of {allowed}")
        return v
    
    class Config:
        env_prefix = "WF_"


class LoggingSettings(BaseSettings):
    """Настройки логирования"""
    level: str = Field(default="INFO", description="Уровень логирования")
    format: str = Field(
        default="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
        description="Формат логов"
    )
    dialog_log_path: Path = Field(default=Path("logs/dialogs.jsonl"), description="Путь к логу диалогов")
    enable_dialog_logging: bool = Field(default=True, description="Включить логирование диалогов")
    
    class Config:
        env_prefix = "LOG_"


class AppSettings(BaseSettings):
    """Главные настройки приложения"""
    # Основные пути
    config_dir: Path = Field(default=Path("config"), description="Директория с конфигами")
    static_dir: Path = Field(default=Path("static"), description="Директория со статикой")
    logs_dir: Path = Field(default=Path("logs"), description="Директория для логов")
    
    # API настройки
    host: str = Field(default="0.0.0.0", description="Хост для запуска сервера")
    port: int = Field(default=8000, ge=1, le=65535, description="Порт сервера")
    
    # Настройки Playwright
    playwright_headless: bool = Field(default=True, description="Headless режим браузера")
    playwright_timeout: int = Field(default=30000, description="Таймаут Playwright (мс)")
    
    # Вложенные настройки
    moderation: ModerationSettings = Field(default_factory=ModerationSettings)
    workflow: WorkflowSettings = Field(default_factory=WorkflowSettings)
    logging: LoggingSettings = Field(default_factory=LoggingSettings)
    
    class Config:
        env_prefix = "APP_"
        env_nested_delimiter = "__"
    
    def ensure_directories(self):
        """Создать необходимые директории"""
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        self.config_dir.mkdir(parents=True, exist_ok=True)
        self.static_dir.mkdir(parents=True, exist_ok=True)


# Глобальный экземпляр настроек
settings = AppSettings()
