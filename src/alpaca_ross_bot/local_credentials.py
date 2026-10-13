"""Read a user-created, local paper credential file without logging its contents."""

import json
import os
import stat
import tempfile
from pathlib import Path

from alpaca_ross_bot.config import ConfigError, PaperConfig

MAX_CREDENTIAL_FILE_BYTES = 16_384


def save_paper_credentials(
    path: Path, key_id: str, secret_key: str, *, replace: bool
) -> None:
    """Atomically save a pair entered at a local hidden prompt."""
    if not key_id.strip() or not secret_key.strip():
        raise ConfigError("paper credentials cannot be empty")
    directory = path.parent
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    details = directory.lstat()
    if not stat.S_ISDIR(details.st_mode) or details.st_uid != os.getuid():
        raise ConfigError("paper secret directory has an invalid owner or type")
    if details.st_mode & 0o077:
        raise ConfigError("paper secret directory permissions are too broad")
    if path.exists() and not replace:
        raise ConfigError("paper credential file already exists; use --replace")
    descriptor, temporary_name = tempfile.mkstemp(prefix=".paper-", dir=directory)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump({"key_id": key_id, "secret_key": secret_key}, stream)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_name, path)
    finally:
        if os.path.exists(temporary_name):
            os.unlink(temporary_name)


def load_paper_credentials(path: Path, config: PaperConfig) -> dict[str, str]:
    flags = os.O_RDONLY | os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise ConfigError("paper credential file is unavailable") from exc
    try:
        details = os.fstat(descriptor)
        if not stat.S_ISREG(details.st_mode) or details.st_uid != os.getuid():
            raise ConfigError("paper credential file has an invalid owner or type")
        if details.st_mode & 0o077:
            raise ConfigError("paper credential file permissions are too broad")
        with os.fdopen(descriptor, "rb") as stream:
            descriptor = -1
            raw = stream.read(MAX_CREDENTIAL_FILE_BYTES + 1)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    if len(raw) > MAX_CREDENTIAL_FILE_BYTES:
        raise ConfigError("paper credential file is too large")
    try:
        payload = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ConfigError("paper credential file is malformed") from exc
    if not isinstance(payload, dict) or set(payload) != {"key_id", "secret_key"}:
        raise ConfigError("paper credential file has invalid fields")
    if not all(isinstance(value, str) and value.strip() for value in payload.values()):
        raise ConfigError("paper credential file has empty fields")
    return {
        config.api_key_id_env: payload["key_id"],
        config.api_secret_key_env: payload["secret_key"],
    }
