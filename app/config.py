from dataclasses import dataclass
import os
from functools import lru_cache
from typing import Optional, List

"""
app/config.py

Centralized configuration for the FastPay payment project.
Loads settings from environment variables (optionally from a .env file).
"""


# Try to load .env if python-dotenv is installed (optional)
try:
    from dotenv import load_dotenv  # type: ignore
    load_dotenv()
except Exception:
    pass


def _parse_bool(value: Optional[str], default: bool = False) -> bool:
    if value is None:
        return default
    return value.lower() in ("1", "true", "yes", "y", "on")


def _parse_int(value: Optional[str], default: int) -> int:
    try:
        return int(value) if value is not None else default
    except ValueError:
        return default


@dataclass(frozen=True)
class Config:
    # App environment
    ENV: str
    DEBUG: bool

    # Security
    SECRET_KEY: str

    # Database
    DATABASE_URL: Optional[str]

    # Payment gateways (keep secrets only in env / secret manager)
    STRIPE_API_KEY: Optional[str]
    STRIPE_WEBHOOK_SECRET: Optional[str]

    PAYPAL_CLIENT_ID: Optional[str]
    PAYPAL_CLIENT_SECRET: Optional[str]

    # Defaults and behavior
    DEFAULT_CURRENCY: str
    PAYMENT_TIMEOUT_SECONDS: int
    PAYMENT_MAX_RETRIES: int
    SUPPORTED_GATEWAYS: List[str]

    # Logging
    LOG_LEVEL: str

    @property
    def any_payment_provider_configured(self) -> bool:
        return any(
            bool(x)
            for x in (
                self.STRIPE_API_KEY,
                self.PAYPAL_CLIENT_ID and self.PAYPAL_CLIENT_SECRET,
            )
        )

    def validate_required(self) -> None:
        """
        Validate that required settings are present. Raise RuntimeError if something critical is missing.
        - SECRET_KEY is required
        - At least one payment provider should be configured
        """
        missing = []
        if not self.SECRET_KEY:
            missing.append("SECRET_KEY")
        if not self.any_payment_provider_configured:
            missing.append("payment provider (STRIPE_API_KEY or PAYPAL_CLIENT_ID+PAYPAL_CLIENT_SECRET)")
        if missing:
            raise RuntimeError("Missing required configuration: " + ", ".join(missing))


@lru_cache()
def get_config() -> Config:
    """
    Build and return a cached Config instance. This function reads environment variables and
    provides sensible defaults. Do not store secrets in source code — set them in env or use a secret manager.
    """
    env = os.getenv("ENV", "development")
    debug = _parse_bool(os.getenv("DEBUG"), default=(env == "development"))

    cfg = Config(
        ENV=env,
        DEBUG=debug,
        SECRET_KEY=os.getenv("SECRET_KEY", ""),  # required, validate later
        DATABASE_URL=os.getenv("DATABASE_URL"),
        STRIPE_API_KEY=os.getenv("STRIPE_API_KEY"),
        STRIPE_WEBHOOK_SECRET=os.getenv("STRIPE_WEBHOOK_SECRET"),
        PAYPAL_CLIENT_ID=os.getenv("PAYPAL_CLIENT_ID"),
        PAYPAL_CLIENT_SECRET=os.getenv("PAYPAL_CLIENT_SECRET"),
        DEFAULT_CURRENCY=os.getenv("DEFAULT_CURRENCY", "USD"),
        PAYMENT_TIMEOUT_SECONDS=_parse_int(os.getenv("PAYMENT_TIMEOUT_SECONDS"), 30),
        PAYMENT_MAX_RETRIES=_parse_int(os.getenv("PAYMENT_MAX_RETRIES"), 3),
        SUPPORTED_GATEWAYS=[g.strip() for g in os.getenv("SUPPORTED_GATEWAYS", "stripe,paypal").split(",") if g.strip()],
        LOG_LEVEL=os.getenv("LOG_LEVEL", "INFO"),
    )

    # Optionally validate now (raise on missing critical config)
    # cfg.validate_required()

    return cfg


# Example usage:
# from app.config import get_config
# cfg = get_config()
# cfg.validate_required()  # call at startup to ensure required secrets exist