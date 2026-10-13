import os
from pathlib import Path

import pytest

from alpaca_ross_bot.config import ConfigError, PaperConfig
from alpaca_ross_bot.local_credentials import (
    load_paper_credentials,
    save_paper_credentials,
)


def test_local_paper_credentials_are_private_and_not_overwritten(
    tmp_path: Path,
) -> None:
    path = tmp_path / "secrets" / "paper.json"
    config = PaperConfig.from_toml(Path("config/paper.example.toml"))
    save_paper_credentials(path, "example-id", "example-secret", replace=False)
    assert path.stat().st_mode & 0o777 == 0o600
    assert path.parent.stat().st_mode & 0o777 == 0o700
    environment = load_paper_credentials(path, config)
    assert environment[config.api_key_id_env] == "example-id"
    assert environment[config.api_secret_key_env] == "example-secret"
    with pytest.raises(ConfigError, match="already exists"):
        save_paper_credentials(path, "replacement", "replacement", replace=False)


def test_local_paper_credentials_reject_unsafe_file(tmp_path: Path) -> None:
    path = tmp_path / "paper.json"
    path.write_text('{"key_id":"example","secret_key":"example"}')
    config = PaperConfig.from_toml(Path("config/paper.example.toml"))
    os.chmod(path, 0o644)
    with pytest.raises(ConfigError, match="permissions"):
        load_paper_credentials(path, config)
    path.unlink()
    path.symlink_to(tmp_path / "missing")
    with pytest.raises(ConfigError, match="unavailable"):
        load_paper_credentials(path, config)
