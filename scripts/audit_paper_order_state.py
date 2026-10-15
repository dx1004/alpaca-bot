"""Read-only account binding and state snapshots; never submit/cancel orders."""

import argparse
import json
import os
from datetime import UTC, datetime
from pathlib import Path

from alpaca_ross_bot.config import PaperConfig
from alpaca_ross_bot.journal import Journal
from alpaca_ross_bot.local_credentials import load_paper_credentials
from alpaca_ross_bot.paper_order_http import PaperOrderHttp


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--connect", action="store_true")
    parser.add_argument(
        "--config", type=Path, default=Path("config/paper.example.toml")
    )
    parser.add_argument("--credentials-file", type=Path, required=True)
    parser.add_argument(
        "--runtime-root",
        type=Path,
        default=Path.home() / ".local/state/alpaca-bot/paper-engineering",
    )
    args = parser.parse_args()
    if not args.connect:
        print(json.dumps({"status": "NOT_RUN", "trading_authorized": False}))
        return
    root = args.runtime_root.resolve()
    project_root = Path(__file__).resolve().parents[1]
    ignored_root = project_root / "var"
    if root.is_relative_to(project_root) and (
        not root.is_relative_to(ignored_root) or root == ignored_root
    ):
        parser.exit(1, "runtime state inside the project must be below ignored var\n")
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    path = root / "journal.sqlite3"
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        pass
    else:
        os.close(descriptor)
    journal = Journal(path, root)
    try:
        config = PaperConfig.from_toml(args.config)
        environment = load_paper_credentials(args.credentials_file, config)
        adapter = PaperOrderHttp(
            config,
            config.resolve_credentials(environment),
            timeout_seconds=5,
            max_request_bytes=65_536,
            max_response_bytes=1_048_576,
        )
        report = adapter.audit_readonly_state(journal)
    except Exception as exc:
        print(
            json.dumps(
                {
                    "status": "FAILED",
                    "error_class": type(exc).__name__,
                    "cause_class": type(exc.__cause__).__name__,
                    "trading_authorized": False,
                },
                sort_keys=True,
            )
        )
        parser.exit(
            1, "read-only paper audit failed; account details were not logged\n"
        )
    finally:
        journal.close()
    report["observed_at"] = datetime.now(UTC).isoformat()
    print(json.dumps(report, sort_keys=True))


if __name__ == "__main__":
    main()
