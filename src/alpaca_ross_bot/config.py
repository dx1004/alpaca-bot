"""Strict paper-only configuration and credential handling.

Alpaca endpoint identities: https://docs.alpaca.markets/us/docs/authentication
"""

import re
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

PAPER_TRADING_URL = "https://paper-api.alpaca.markets"
MARKET_DATA_URL = "https://data.alpaca.markets"
_CONFIG_KEYS = frozenset(
    {
        "mode",
        "trading_base_url",
        "market_data_base_url",
        "api_key_id_env",
        "api_secret_key_env",
    }
)
_ENV_NAME = re.compile(r"[A-Z][A-Z0-9_]*\Z")
_SENSITIVE_HEADERS = frozenset(
    {"apca-api-key-id", "apca-api-secret-key", "authorization"}
)


class ConfigError(ValueError):
    """Configuration is missing, invalid, or points outside paper trading."""


@dataclass(frozen=True, repr=False)
class SecretValue:
    _value: str

    def __repr__(self) -> str:
        return "<redacted>"

    __str__ = __repr__

    def reveal(self) -> str:
        """Return the value only at the authenticated transport boundary."""
        return self._value


@dataclass(frozen=True, repr=False)
class PaperCredentials:
    key_id: SecretValue
    secret_key: SecretValue

    def __repr__(self) -> str:
        return "PaperCredentials(<redacted>)"

    def auth_headers(self) -> dict[str, str]:
        """Build headers for a validated paper endpoint; never log this mapping."""
        return {
            "APCA-API-KEY-ID": self.key_id.reveal(),
            "APCA-API-SECRET-KEY": self.secret_key.reveal(),
        }


@dataclass(frozen=True)
class PaperConfig:
    trading_base_url: str
    market_data_base_url: str
    api_key_id_env: str
    api_secret_key_env: str

    @classmethod
    def from_mapping(cls, values: Mapping[str, object]) -> "PaperConfig":
        if set(values) != _CONFIG_KEYS:
            raise ConfigError(
                "paper configuration requires exactly the documented keys"
            )
        if any(not isinstance(value, str) or not value for value in values.values()):
            raise ConfigError("paper configuration values must be nonempty strings")
        if values["mode"] != "paper":
            raise ConfigError("only paper mode is supported")
        if values["trading_base_url"] != PAPER_TRADING_URL:
            raise ConfigError("trading endpoint must be the exact Alpaca paper URL")
        if values["market_data_base_url"] != MARKET_DATA_URL:
            raise ConfigError("market data endpoint must be the exact Alpaca data URL")
        key_id_env = values["api_key_id_env"]
        secret_key_env = values["api_secret_key_env"]
        if not isinstance(key_id_env, str) or not isinstance(secret_key_env, str):
            raise ConfigError("secret references must be environment variable names")
        if not _ENV_NAME.fullmatch(key_id_env) or not _ENV_NAME.fullmatch(
            secret_key_env
        ):
            raise ConfigError("secret references must be environment variable names")
        if key_id_env == secret_key_env:
            raise ConfigError("key ID and secret must use separate references")
        return cls(
            trading_base_url=PAPER_TRADING_URL,
            market_data_base_url=MARKET_DATA_URL,
            api_key_id_env=key_id_env,
            api_secret_key_env=secret_key_env,
        )

    @classmethod
    def from_toml(cls, path: Path) -> "PaperConfig":
        with path.open("rb") as config_file:
            return cls.from_mapping(tomllib.load(config_file))

    def resolve_credentials(self, environment: Mapping[str, str]) -> PaperCredentials:
        key_id = environment.get(self.api_key_id_env)
        secret_key = environment.get(self.api_secret_key_env)
        if not key_id or not secret_key or not key_id.strip() or not secret_key.strip():
            raise ConfigError("paper credentials are unavailable")
        return PaperCredentials(SecretValue(key_id), SecretValue(secret_key))


def redact_http_headers(headers: Mapping[str, str]) -> dict[str, str]:
    """Produce log-safe HTTP headers, preserving only nonsensitive values."""
    return {
        name: "<redacted>" if name.lower() in _SENSITIVE_HEADERS else value
        for name, value in headers.items()
    }
