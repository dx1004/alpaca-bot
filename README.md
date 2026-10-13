# Alpaca Ross Bot

This repository is an evidence-first, paper-only development project for a
Ross Cameron small-account strategy on Alpaca. It does not contain an enabled
trading service.

API integration is planned around the [official Alpaca Python SDK reuse review](docs/alpaca-existing-tools-review.md); the Ross strategy, evidence gates and reconciliation remain project-owned.

## Development setup

From the repository root, use `uv` 0.12.17 or a compatible version. The project
pins Python 3.12.14 in `.python-version` and locks all dependencies in `uv.lock`.

```bash
rtk proxy env UV_CACHE_DIR="$PWD/var/uv-cache" uv sync --locked --all-groups
rtk proxy env UV_CACHE_DIR="$PWD/var/uv-cache" uv run --locked ruff check src tests
rtk proxy env UV_CACHE_DIR="$PWD/var/uv-cache" uv run --locked mypy --strict src tests
rtk proxy env UV_CACHE_DIR="$PWD/var/uv-cache" uv run --locked pytest
```

The cache, virtual environment, local configuration secrets and runtime data
remain outside Git. Strategy and broker functionality are added only after
their evidence and safety gates in [TODO.md](TODO.md) pass.

## Paper credentials

If you already have a paper Key ID and its matching Secret Key, use that pair.
Enter both directly in the local WSL terminal; both prompts hide input. The
file is stored outside Git at `~/.local/share/alpaca-bot/paper.json` with
owner-only permissions, and is never printed by the probe. The repository's
`/mnt/t` DriveFS mount does not preserve the required private Unix file modes.

```bash
rtk proxy env UV_CACHE_DIR="$PWD/var/uv-cache" uv run --locked python scripts/configure_paper_credentials.py
rtk proxy env UV_CACHE_DIR="$PWD/var/uv-cache" uv run --locked python scripts/probe_alpaca_capabilities.py --credentials-file "$HOME/.local/share/alpaca-bot/paper.json" --connect
```

Use `--replace` on the first command only when intentionally replacing a
previously saved paper pair. The second command performs read-only account,
data and WebSocket capability checks; it does not place orders. Keep the
credential file on this machine and never paste its contents into chat.
