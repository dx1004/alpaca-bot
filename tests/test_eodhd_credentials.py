import getpass
import io
import json
import os
import runpy
import sys
import warnings
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

import pytest

from alpaca_ross_bot.config import ConfigError, SecretValue
from alpaca_ross_bot.eodhd_credentials import (
    MAX_FILE_BYTES,
    MAX_TOKEN_BYTES,
    load_eodhd_token,
    save_eodhd_token,
)

TOKEN = "test-only-token"


def _path(tmp_path: Path) -> Path:
    return tmp_path / "private" / "eodhd-reference.json"


def _write(path: Path, raw: bytes) -> None:
    path.parent.mkdir(mode=0o700, exist_ok=True)
    path.write_bytes(raw)
    path.chmod(0o600)


def test_roundtrip_is_private_redacted_and_separate_from_paper(tmp_path: Path) -> None:
    path = _path(tmp_path)
    save_eodhd_token(path, TOKEN)
    loaded = load_eodhd_token(path)
    assert isinstance(loaded, SecretValue) and loaded.reveal() == TOKEN
    assert str(loaded) == repr(loaded) == "<redacted>"
    assert path.stat().st_mode & 0o777 == 0o600
    assert path.parent.stat().st_mode & 0o777 == 0o700
    assert json.loads(path.read_bytes()) == {"api_token": TOKEN}
    assert not (path.parent / "paper.json").exists()
    assert not list(path.parent.glob(".eodhd-*"))


@pytest.mark.parametrize(
    "token",
    [
        "",
        " ",
        "a b",
        "a\nb",
        "a\x00b",
        "a\x7fb",
        "a\u200bb",
        "a" * (MAX_TOKEN_BYTES + 1),
    ],
)
def test_empty_control_or_oversize_tokens_are_not_saved(
    tmp_path: Path, token: str
) -> None:
    path = _path(tmp_path)
    with pytest.raises(ConfigError):
        save_eodhd_token(path, token)
    assert not path.exists()


def test_existing_file_requires_explicit_replace(tmp_path: Path) -> None:
    path = _path(tmp_path)
    save_eodhd_token(path, TOKEN)
    original = path.read_bytes()
    with pytest.raises(ConfigError, match="already exists"):
        save_eodhd_token(path, "different")
    assert path.read_bytes() == original
    save_eodhd_token(path, "replacement", replace=True)
    assert load_eodhd_token(path).reveal() == "replacement"


@pytest.mark.parametrize("dangling", [False, True])
@pytest.mark.parametrize("replace", [False, True])
def test_symlinks_never_followed_or_replaced(
    tmp_path: Path, dangling: bool, replace: bool
) -> None:
    path = _path(tmp_path)
    path.parent.mkdir(mode=0o700)
    target = tmp_path / "target"
    if not dangling:
        target.write_text("unchanged")
    path.symlink_to(target)
    with pytest.raises(ConfigError):
        save_eodhd_token(path, TOKEN, replace=replace)
    with pytest.raises(ConfigError):
        load_eodhd_token(path)
    assert path.is_symlink()
    assert not target.exists() if dangling else target.read_text() == "unchanged"


def test_parent_symlink_and_unsafe_permissions_are_rejected(tmp_path: Path) -> None:
    target = tmp_path / "real"
    target.mkdir(mode=0o700)
    alias = tmp_path / "alias"
    alias.symlink_to(target)
    with pytest.raises(ConfigError):
        save_eodhd_token(alias / "token.json", TOKEN)
    path = _path(tmp_path)
    path.parent.mkdir(mode=0o755)
    for action in (
        lambda: save_eodhd_token(path, TOKEN),
        lambda: load_eodhd_token(path),
    ):
        with pytest.raises(ConfigError, match="directory"):
            action()


def test_unsafe_file_permissions_and_nonregular_files_are_rejected(
    tmp_path: Path,
) -> None:
    path = _path(tmp_path)
    _write(path, b'{"api_token":"test"}')
    path.chmod(0o644)
    with pytest.raises(ConfigError, match="private regular"):
        load_eodhd_token(path)
    with pytest.raises(ConfigError, match="private regular"):
        save_eodhd_token(path, TOKEN, replace=True)
    path.unlink()
    os.mkfifo(path, mode=0o600)
    with pytest.raises(ConfigError, match="private regular"):
        load_eodhd_token(path)


def test_parent_owner_mismatch_is_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = _path(tmp_path)
    _write(path, b'{"api_token":"test"}')
    real_uid = os.getuid()
    monkeypatch.setattr(os, "getuid", lambda: real_uid + 1)
    with pytest.raises(ConfigError, match="directory"):
        load_eodhd_token(path)


def test_file_owner_mismatch_is_rejected_after_private_parent_check(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = _path(tmp_path)
    _write(path, b'{"api_token":"test"}')
    real_uid = os.getuid()
    checks = iter((real_uid, real_uid + 1))
    monkeypatch.setattr(os, "getuid", lambda: next(checks))
    with pytest.raises(ConfigError, match="private regular"):
        load_eodhd_token(path)


@pytest.mark.parametrize(
    "raw",
    [
        b"invalid",
        b"\xff",
        b"[]",
        b'{"api_token":"first","api_token":"second"}',
        b'{"api_token":"test","other":"field"}',
        b'{"api_token":true}',
        b'{"api_token":null}',
        b'{"api_token":""}',
        b'{"api_token":"a\\nb"}',
        b" " * (MAX_FILE_BYTES + 1),
    ],
)
def test_bounded_strict_token_only_load(tmp_path: Path, raw: bytes) -> None:
    path = _path(tmp_path)
    _write(path, raw)
    with pytest.raises(ConfigError):
        load_eodhd_token(path)


def test_no_clobber_handles_a_creation_race(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = _path(tmp_path)
    original_link = os.link

    def collide(source: str, destination: str, **kwargs: Any) -> None:
        path.write_bytes(b"raced file")
        path.chmod(0o600)
        original_link(source, destination, **kwargs)

    monkeypatch.setattr(os, "link", collide)
    with pytest.raises(ConfigError, match="already exists"):
        save_eodhd_token(path, TOKEN)
    assert path.read_bytes() == b"raced file"
    assert not list(path.parent.glob(".eodhd-*"))


class Terminal(io.StringIO):
    def isatty(self) -> bool:
        return True


def _cli(path: Path, monkeypatch: pytest.MonkeyPatch, *args: str) -> Callable[[], None]:
    namespace = runpy.run_path("scripts/configure_eodhd_credentials.py")
    main = cast(Callable[[], None], namespace["main"])
    cast(Any, main).__globals__["DEFAULT_PATH"] = path
    monkeypatch.setattr(sys, "argv", ["configure_eodhd_credentials.py", *args])
    return main


def test_noninteractive_cli_does_not_request_input(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = _path(tmp_path)
    main = _cli(path, monkeypatch)
    monkeypatch.setattr(sys, "stdin", io.StringIO())

    def no_input(*args: object, **kwargs: object) -> str:
        raise AssertionError("input must not be requested")

    monkeypatch.setattr(getpass, "getpass", no_input)
    with pytest.raises(SystemExit) as result:
        main()
    assert result.value.code == 1 and not path.exists()


def test_hidden_prompt_warning_never_echoes_or_saves(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    path = _path(tmp_path)
    main = _cli(path, monkeypatch)
    stderr = Terminal()
    monkeypatch.setattr(sys, "stdin", Terminal())
    monkeypatch.setattr(sys, "stderr", stderr)

    def unavailable(*args: object, **kwargs: object) -> str:
        warnings.warn(TOKEN, getpass.GetPassWarning, stacklevel=1)
        raise AssertionError("warning must stop before echo fallback")

    monkeypatch.setattr(getpass, "getpass", unavailable)
    with pytest.raises(SystemExit) as result:
        main()
    assert result.value.code == 1 and not path.exists()
    output = capsys.readouterr()
    assert TOKEN not in output.out + output.err + stderr.getvalue()


@pytest.mark.parametrize("error", [EOFError, KeyboardInterrupt])
def test_hidden_input_cancellation_is_clean(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, error: type[BaseException]
) -> None:
    path = _path(tmp_path)
    main = _cli(path, monkeypatch)
    monkeypatch.setattr(sys, "stdin", Terminal())
    monkeypatch.setattr(sys, "stderr", Terminal())

    def cancelled(*args: object, **kwargs: object) -> str:
        raise error

    monkeypatch.setattr(getpass, "getpass", cancelled)
    with pytest.raises(SystemExit) as result:
        main()
    assert result.value.code == 1 and not path.exists()


def test_check_cli_validates_without_input_or_token_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    path = _path(tmp_path)
    save_eodhd_token(path, TOKEN)
    main = _cli(path, monkeypatch, "--check")
    monkeypatch.setattr(sys, "stdin", io.StringIO())
    main()
    output = capsys.readouterr()
    assert "contents hidden" in output.out and TOKEN not in output.out + output.err


def test_tty_input_with_rtk_captured_stderr_uses_default_hidden_prompt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    path = _path(tmp_path)
    main = _cli(path, monkeypatch)
    stderr = io.StringIO()
    monkeypatch.setattr(sys, "stdin", Terminal())
    monkeypatch.setattr(sys, "stderr", stderr)

    def hidden(prompt: str) -> str:
        # Leave getpass's stream unspecified so it uses the controlling terminal,
        # even when RTK captures stderr and buffers its output.
        assert "hidden" in prompt and not stderr.isatty()
        return TOKEN

    monkeypatch.setattr(getpass, "getpass", hidden)
    main()
    assert load_eodhd_token(path).reveal() == TOKEN
    output = capsys.readouterr()
    assert "contents hidden" in output.out
    assert TOKEN not in output.out + output.err + stderr.getvalue()


def test_serialized_token_size_limit_cannot_create_an_unloadable_file(
    tmp_path: Path,
) -> None:
    path = _path(tmp_path)
    with pytest.raises(ConfigError, match="size limit"):
        save_eodhd_token(path, '"' * MAX_TOKEN_BYTES)
    assert not path.exists()
