"""Production settings loaded from environment variables."""
from __future__ import annotations

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(".env", ".env.local"),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # Server
    host: str = "0.0.0.0"
    port: int = 8000
    environment: str = "development"
    log_level: str = "INFO"

    # App
    app_name: str = "Production AI Agent"
    app_version: str = "1.0.0"

    # Storage
    redis_url: str = ""

    # Security
    agent_api_key: str = ""

    # Runtime limits
    rate_limit_per_minute: int = 10
    monthly_budget_usd: float = 10.0
    conversation_ttl_seconds: int = 86400
    max_history_messages: int = 20

    # LLM
    openrouter_api_key: str = ""
    openrouter_model: str = "google/gemini-2.5-flash-lite"
    openrouter_base_url: str = "https://openrouter.ai/api/v1"

    # CORS
    allowed_origins_raw: str = Field(
        default="http://localhost:3000",
        validation_alias="ALLOWED_ORIGINS",
    )

    @field_validator("environment", mode="before")
    @classmethod
    def normalize_environment(cls, value: str) -> str:
        if isinstance(value, str):
            value = value.strip().lower()
        if value not in {"development", "staging", "production"}:
            raise ValueError("ENVIRONMENT must be one of: development, staging, production")
        return value

    @field_validator("log_level", mode="before")
    @classmethod
    def normalize_log_level(cls, value: str) -> str:
        if isinstance(value, str):
            value = value.strip().upper()
        if value not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
            raise ValueError("LOG_LEVEL must be one of: DEBUG, INFO, WARNING, ERROR, CRITICAL")
        return value

    @model_validator(mode="after")
    def validate_runtime_settings(self) -> "Settings":
        if self.port <= 0:
            raise ValueError("PORT must be greater than 0")
        if self.rate_limit_per_minute <= 0:
            raise ValueError("RATE_LIMIT_PER_MINUTE must be greater than 0")
        if self.monthly_budget_usd < 0:
            raise ValueError("MONTHLY_BUDGET_USD must be greater than or equal to 0")
        if self.conversation_ttl_seconds <= 0:
            raise ValueError("CONVERSATION_TTL_SECONDS must be greater than 0")
        if self.max_history_messages < 2:
            raise ValueError("MAX_HISTORY_MESSAGES must be at least 2")

        if self.environment == "production":
            if not self.agent_api_key:
                raise ValueError("AGENT_API_KEY must be set in production")
            if not self.redis_url:
                raise ValueError("REDIS_URL must be set in production")
            if not self.openrouter_api_key:
                raise ValueError("OPENROUTER_API_KEY must be set in production")
        return self

    @property
    def debug(self) -> bool:
        return self.log_level == "DEBUG"

    @property
    def llm_model(self) -> str:
        return self.openrouter_model

    @property
    def openai_api_key(self) -> str:
        return self.openrouter_api_key

    @property
    def daily_budget_usd(self) -> float:
        return self.monthly_budget_usd

    @property
    def allowed_origins(self) -> list[str]:
        return [origin.strip() for origin in self.allowed_origins_raw.split(",") if origin.strip()]


settings = Settings()
