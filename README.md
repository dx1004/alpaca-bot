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

To bind the engineering journal to the authenticated paper account and capture
read-only open-order/position snapshots, run:

```bash
rtk proxy .venv/bin/python scripts/audit_paper_order_state.py --credentials-file "$HOME/.local/share/alpaca-bot/paper.json" --runtime-root "$HOME/.local/state/alpaca-bot/paper-engineering" --connect
```

This audit uses GET requests only. It keeps account responses in the private WSL
runtime directory (0700, database 0600) and prints only verification flags. The
Windows-backed project drive cannot enforce those permissions. Empty snapshots
do not resolve an uncertain submission or authorize trading. Without `--connect`,
the script reports `NOT_RUN`.

To verify the pinned SDK's paper trade-update subscription together with bounded
REST order/fill/position recovery, run:

```bash
rtk proxy .venv/bin/python scripts/reconcile_paper_orders.py --credentials-file "$HOME/.local/share/alpaca-bot/paper.json" --runtime-root "$HOME/.local/state/alpaca-bot/paper-engineering" --connect
```

This finite probe only reads the broker and records private local evidence. On
the first run, `--initialize-baseline` can register an empty starting inventory
only after a fresh, complete account capture confirms no open orders, positions
or local intents. Otherwise the missing baseline remains an explicit difference;
do not delete state or reset it to bypass a discrepancy. The component API also
supports a source-linked long-position baseline before any local intent.
`MATCHED` describes the captured reconciliation, not an atomic broker snapshot
or trading authority. Shutdown and restart require recovery again. Real order
cycles and protection/incident behavior remain separate W3-06/W3-07 gates.

To check only access to Alpaca's SIP-based market-ranker endpoints, use
`--sip-screeners-only --connect` with the same credential file. This sends
only two read-only GET requests and skips account, quote/trade, news, and
WebSocket checks. A successful response is reported as
`REACHABLE_SCHEMA_UNVERIFIED`; it does not establish complete market scanning,
Ross fidelity, or `sip_equivalence_verified`.
