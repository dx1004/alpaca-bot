from pathlib import Path

import pytest

from alpaca_ross_bot.config import (
    ConfigError,
    PaperConfig,
    redact_http_headers,
)

PAPER_VALUES: dict[str, object] = {
    "mode": "paper",
    "trading_base_url": "https://paper-api.alpaca.markets",
    "market_data_base_url": "https://data.alpaca.markets",
    "api_key_id_env": "ALPACA_PAPER_KEY_ID",
    "api_secret_key_env": "ALPACA_PAPER_SECRET_KEY",
}


def test_example_is_valid() -> None:
    config = PaperConfig.from_toml(Path("config/paper.example.toml"))
    assert config.trading_base_url == "https://paper-api.alpaca.markets"


@pytest.mark.parametrize(
    ("changed_key", "changed_value"),
    [
        ("mode", "live"),
        ("trading_base_url", "https://api.alpaca.markets"),
        ("trading_base_url", "https://broker-api.alpaca.markets"),
        ("trading_base_url", "https://paper-api.alpaca.markets.evil.test"),
        ("trading_base_url", "https://paper-api.alpaca.markets/path"),
        ("market_data_base_url", "https://data.sandbox.alpaca.markets"),
        ("api_key_id_env", "literal-secret"),
        ("api_secret_key_env", "ALPACA_PAPER_KEY_ID"),
    ],
)
def test_wrong_mode_endpoint_or_secret_reference_fails(
    changed_key: str, changed_value: str
) -> None:
    values = PAPER_VALUES | {changed_key: changed_value}
    with pytest.raises(ConfigError):
        PaperConfig.from_mapping(values)


def test_missing_or_unknown_config_field_fails() -> None:
    with pytest.raises(ConfigError):
        PaperConfig.from_mapping(
            {key: value for key, value in PAPER_VALUES.items() if key != "mode"}
        )
    with pytest.raises(ConfigError):
        PaperConfig.from_mapping(PAPER_VALUES | {"extra": "unexpected"})


def test_missing_credentials_fail_without_leaking_values() -> None:
    config = PaperConfig.from_mapping(PAPER_VALUES)
    with pytest.raises(ConfigError, match="unavailable"):
        config.resolve_credentials({"ALPACA_PAPER_KEY_ID": "sensitive-key-id"})


def test_credentials_and_header_logs_are_redacted(
    capsys: pytest.CaptureFixture[str],
) -> None:
    config = PaperConfig.from_mapping(PAPER_VALUES)
    credentials = config.resolve_credentials(
        {
            "ALPACA_PAPER_KEY_ID": "sensitive-key-id",
            "ALPACA_PAPER_SECRET_KEY": "sensitive-secret-key",
        }
    )
    headers = credentials.auth_headers()
    print(credentials)
    print(redact_http_headers(headers | {"Accept": "application/json"}))
    printed = capsys.readouterr().out
    assert "sensitive-key-id" not in printed
    assert "sensitive-secret-key" not in printed
    assert headers["APCA-API-KEY-ID"] == "sensitive-key-id"
    assert redact_http_headers({"Authorization": "Bearer token"}) == {
        "Authorization": "<redacted>"
    }
