import os
from datetime import date
from decimal import Decimal
from functools import lru_cache
from pathlib import Path
from typing import Literal
from urllib.parse import urlparse

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(os.environ.get("APP_ROOT", str(Path(__file__).resolve().parents[2])))


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=ROOT / ".env", extra="ignore", hide_input_in_errors=True
    )
    mode: Literal["demo", "controlled", "live", "test"] = "demo"
    database_url: str = "postgresql+psycopg://ai_leads_demo@127.0.0.1:15432/ai_leads_demo"
    business_config: Path = ROOT / "config" / "automation.yaml"
    public_url: str = "http://localhost:5173"
    allowed_origins: str = (
        "http://localhost:5173,http://127.0.0.1:5173,http://localhost:8000,http://127.0.0.1:8000"
    )
    secure_cookies: bool = False
    internal_token: SecretStr = SecretStr("")
    n8n_webhook_token: SecretStr = SecretStr("")
    n8n_base_url: str = "http://127.0.0.1:5681"
    session_hours: int = 8
    openai_api_key: SecretStr = SecretStr("")
    telegram_bot_token: SecretStr = SecretStr("")
    telegram_chat_id: str = ""
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_username: str = ""
    smtp_password: SecretStr = SecretStr("")
    smtp_from: str = ""
    imap_host: str = ""
    imap_port: int = 993
    imap_username: str = ""
    imap_password: SecretStr = SecretStr("")
    imap_folder: str = "INBOX"
    allow_external_sends: bool = False
    real_draft_enabled: bool = False
    controlled_ai_request_limit: int = Field(default=1, ge=1, le=1)
    controlled_ai_max_cost_usd: Decimal = Field(default=Decimal("0.02"), gt=0, le=Decimal("0.02"))
    ai_daily_token_budget: int = 100_000
    ai_max_parallel: int = 2
    ai_input_price_per_million: Decimal | None = Field(default=None, ge=0)
    ai_output_price_per_million: Decimal | None = Field(default=None, ge=0)
    ai_price_model: str | None = None
    ai_price_source: str | None = None
    ai_price_as_of: date | None = None
    ai_price_currency: str = Field(default="USD", pattern=r"^[A-Z]{3}$")
    max_job_attempts: int = 5
    job_lease_seconds: int = 180
    request_limit_per_minute: int = 10

    @model_validator(mode="after")
    def guard_mode(self):
        prices = (
            self.ai_input_price_per_million,
            self.ai_output_price_per_million,
            self.ai_price_model,
            self.ai_price_source,
            self.ai_price_as_of,
        )
        if any(value is not None for value in prices) and not all(
            value is not None and value != "" for value in prices
        ):
            raise ValueError("AI pricing requires both rates, exact model, source and date")
        if not self.database_url.startswith("postgresql+psycopg://"):
            raise ValueError("Only PostgreSQL with psycopg is supported")
        if self.mode in {"demo", "test"}:
            if any(
                s.get_secret_value()
                for s in (
                    self.openai_api_key,
                    self.telegram_bot_token,
                    self.smtp_password,
                    self.imap_password,
                )
            ):
                raise ValueError("Demo/test must not receive live credentials")
            if (
                self.allow_external_sends
                or self.smtp_host
                or self.imap_host
                or self.real_draft_enabled
            ):
                raise ValueError("Demo/test cannot configure real communication")
        elif self.mode == "controlled":
            if any(
                s.get_secret_value()
                for s in (self.telegram_bot_token, self.smtp_password, self.imap_password)
            ):
                raise ValueError("Controlled mode only permits OpenAI credentials")
            if (
                self.allow_external_sends
                or self.smtp_host
                or self.imap_host
                or self.telegram_chat_id
            ):
                raise ValueError("Controlled communication must remain simulated")
            if urlparse(self.public_url).hostname not in {"localhost", "127.0.0.1"}:
                raise ValueError("Controlled mode is loopback only")
            if (
                len(self.internal_token.get_secret_value()) < 32
                or len(self.n8n_webhook_token.get_secret_value()) < 32
                or self.internal_token == self.n8n_webhook_token
            ):
                raise ValueError("Controlled requires distinct strong internal credentials")
            parsed = urlparse(self.database_url.replace("+psycopg", ""))
            if parsed.hostname != "127.0.0.1" or parsed.path != "/ai_leads_controlled":
                raise ValueError("Controlled requires its isolated local synthetic database")
        else:
            if not self.secure_cookies or urlparse(self.public_url).scheme != "https":
                raise ValueError("Live requires HTTPS and secure cookies")
            if len(self.internal_token.get_secret_value()) < 32:
                raise ValueError("Live requires a strong internal token")
            if len(self.n8n_webhook_token.get_secret_value()) < 32:
                raise ValueError("Live requires a separate strong webhook token")
            if self.internal_token == self.n8n_webhook_token:
                raise ValueError("Internal and webhook credentials must differ")
            parsed = urlparse(self.database_url.replace("+psycopg", ""))
            if not parsed.password:
                raise ValueError("Live database requires credentials")
        return self

    @property
    def origins(self) -> set[str]:
        return {value.strip() for value in self.allowed_origins.split(",") if value.strip()}


@lru_cache
def get_settings() -> Settings:
    return Settings()
