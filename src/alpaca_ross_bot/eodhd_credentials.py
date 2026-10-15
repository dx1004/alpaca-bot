"""Private local EODHD token storage, with no transport or environment export."""

import json
import os
import stat
import unicodedata
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

from alpaca_ross_bot.config import ConfigError, SecretValue

DEFAULT_PATH = Path.home() / ".local/share/alpaca-bot/eodhd-reference.json"
MAX_FILE_BYTES = 8192
MAX_TOKEN_BYTES = 4096


def _token(value: object) -> str:
    if (
        not isinstance(value, str)
        or not value
        or any(
            char.isspace() or unicodedata.category(char).startswith("C")
            for char in value
        )
        or len(value.encode("utf-8")) > MAX_TOKEN_BYTES
    ):
        raise ConfigError("EODHD token is empty or invalid")
    return value


@contextmanager
def _directory(path: Path, *, create: bool) -> Iterator[int]:
    if create:
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    descriptor = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        details = os.fstat(descriptor)
        if (
            not stat.S_ISDIR(details.st_mode)
            or details.st_uid != os.getuid()
            or stat.S_IMODE(details.st_mode) != 0o700
        ):
            raise ConfigError("EODHD secret directory must be owned and private (0700)")
        yield descriptor
    finally:
        os.close(descriptor)


def _private_file(details: os.stat_result) -> None:
    if (
        not stat.S_ISREG(details.st_mode)
        or details.st_uid != os.getuid()
        or stat.S_IMODE(details.st_mode) != 0o600
    ):
        raise ConfigError(
            "EODHD credentials require an owned private regular file (0600)"
        )


def save_eodhd_token(path: Path, token: str, *, replace: bool = False) -> None:
    """Publish a private token file; only explicit replacement can overwrite."""
    payload = json.dumps({"api_token": _token(token)}, ensure_ascii=False).encode(
        "utf-8"
    )
    if len(payload) > MAX_FILE_BYTES:
        raise ConfigError("EODHD credential file exceeds its size limit")
    try:
        with _directory(path, create=True) as directory:
            existing = None
            if replace:
                try:
                    existing = os.stat(
                        path.name, dir_fd=directory, follow_symlinks=False
                    )
                    _private_file(existing)
                except FileNotFoundError:
                    pass
            temporary = ".eodhd-" + uuid4().hex
            descriptor = os.open(
                temporary,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                0o600,
                dir_fd=directory,
            )
            try:
                with os.fdopen(descriptor, "wb") as stream:
                    os.fchmod(stream.fileno(), 0o600)
                    stream.write(payload)
                    stream.flush()
                    os.fsync(stream.fileno())
                if existing is None:
                    os.link(
                        temporary,
                        path.name,
                        src_dir_fd=directory,
                        dst_dir_fd=directory,
                        follow_symlinks=False,
                    )
                else:
                    os.replace(
                        temporary, path.name, src_dir_fd=directory, dst_dir_fd=directory
                    )
                os.fsync(directory)
            finally:
                try:
                    os.unlink(temporary, dir_fd=directory)
                except FileNotFoundError:
                    pass
    except FileExistsError:
        raise ConfigError(
            "EODHD credential file already exists; use --replace"
        ) from None
    except OSError:
        raise ConfigError("EODHD credential file could not be saved safely") from None


def load_eodhd_token(path: Path = DEFAULT_PATH) -> SecretValue:
    """Read a bounded, private token-only file and return a redacted wrapper."""
    try:
        with _directory(path, create=False) as directory:
            descriptor = os.open(
                path.name,
                os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                dir_fd=directory,
            )
            with os.fdopen(descriptor, "rb") as stream:
                _private_file(os.fstat(stream.fileno()))
                raw = stream.read(MAX_FILE_BYTES + 1)
    except OSError:
        raise ConfigError("EODHD credential file is unavailable or unsafe") from None
    if len(raw) > MAX_FILE_BYTES:
        raise ConfigError("EODHD credential file exceeds its size limit")
    try:
        payload = json.loads(raw, object_pairs_hook=_unique_pairs)
    except (ValueError, UnicodeDecodeError):
        raise ConfigError("EODHD credential file is malformed") from None
    if not isinstance(payload, dict) or set(payload) != {"api_token"}:
        raise ConfigError("EODHD credential file must contain only its token")
    return SecretValue(_token(payload["api_token"]))


def _unique_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate field")
        result[key] = value
    return result
