"""Application settings, loaded from environment variables (twelve-factor style)."""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_DEV_JWT_SECRET = "dev-only-secret-change-me-dev-only-secret"  # noqa: S105 - rejected in production


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="LEDGER_", env_file=".env", extra="ignore")

    environment: Literal["development", "test", "production"] = "development"

    database_url: str = "postgresql+psycopg://postgres:postgres@localhost:5432/ledger"
    db_pool_size: int = Field(default=10, ge=1)
    db_max_overflow: int = Field(default=10, ge=0)

    jwt_secret: SecretStr = SecretStr(_DEV_JWT_SECRET)
    jwt_issuer: str = "ledgerline"
    jwt_audience: str = "ledgerline-api"
    access_token_ttl_minutes: int = Field(default=15, ge=1, le=24 * 60)

    supported_currencies: tuple[str, ...] = ("USD", "EUR", "GBP", "JPY")
    transfer_fee_bps: int = Field(default=0, ge=0, le=1_000, description="Fee in basis points")

    rate_limit_capacity: int = Field(default=20, ge=1)
    rate_limit_refill_per_second: float = Field(default=5.0, gt=0)
    login_rate_limit_capacity: int = Field(default=10, ge=1)
    login_rate_limit_refill_per_second: float = Field(default=0.2, gt=0)

    idempotency_key_ttl_hours: int = Field(default=24, ge=1)

    log_level: str = "INFO"
    log_json: bool = True

    webhook_url: str | None = None
    webhook_secret: SecretStr | None = None
    outbox_poll_interval_seconds: float = Field(default=1.0, gt=0)
    outbox_batch_size: int = Field(default=100, ge=1, le=1_000)
    outbox_max_attempts: int = Field(default=10, ge=1)

    @field_validator("supported_currencies", mode="after")
    @classmethod
    def _normalise_currencies(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(code.strip().upper() for code in value)

    @field_validator("webhook_url", mode="before")
    @classmethod
    def _empty_string_means_unset(cls, value: object) -> object:
        return None if value == "" else value

    @model_validator(mode="after")
    def _refuse_insecure_production_config(self) -> Settings:
        if self.environment == "production":
            secret = self.jwt_secret.get_secret_value()
            if secret == _DEV_JWT_SECRET or len(secret) < 32:
                raise ValueError(
                    "LEDGER_JWT_SECRET must be a unique value of 32+ chars in production"
                )
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
