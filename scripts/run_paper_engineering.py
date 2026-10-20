"""Finite isolated recovery only; this CLI has no broker-write switch."""

import argparse
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from alpaca_ross_bot.config import PaperConfig
from alpaca_ross_bot.local_credentials import load_paper_credentials
from alpaca_ross_bot.order_reconciliation import ReconciliationPolicy
from alpaca_ross_bot.paper_engineering import EngineeringHarness, EngineeringScope
from alpaca_ross_bot.paper_order_contract import decode_broker_json
from alpaca_ross_bot.paper_order_http import PaperOrderHttp


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--recover-readonly", action="store_true")
    parser.add_argument("--scope-file", type=Path, required=True)
    parser.add_argument("--runtime-root", type=Path, required=True)
    parser.add_argument("--credentials-file", type=Path, required=True)
    parser.add_argument(
        "--formal-credentials-file",
        type=Path,
        help="Authenticate the distinct formal account; never grants cleanup or orders",
    )
    parser.add_argument(
        "--config", type=Path, default=Path("config/paper.example.toml")
    )
    args = parser.parse_args()
    if not args.recover_readonly:
        print(json.dumps({"status": "NOT_RUN", "runtime_authorized": False}))
        return
    harness: EngineeringHarness | None = None
    try:
        # The scope contains private opaque identity references, not credentials.
        raw = decode_broker_json(args.scope_file.read_bytes())
        names = {
            "experiment_id",
            "account_ref",
            "formal_account_ref",
            "formal_runtime_root",
        }
        if (
            not isinstance(raw, dict)
            or set(raw) != names
            or any(
                not isinstance(value, str) or not value.strip()
                for value in raw.values()
            )
        ):
            raise ValueError("invalid engineering scope")
        scope = EngineeringScope(
            raw["experiment_id"],
            raw["account_ref"],
            raw["formal_account_ref"],
            Path(raw["formal_runtime_root"]),
        )
        scope.validate(args.runtime_root)
        config = PaperConfig.from_toml(args.config)
        credentials = config.resolve_credentials(
            load_paper_credentials(args.credentials_file, config)
        )
        harness = EngineeringHarness(
            args.runtime_root, scope, clock=lambda: datetime.now(UTC)
        )
        api = PaperOrderHttp(
            config,
            credentials,
            scope.account_ref,
            timeout_seconds=5,
            max_request_bytes=65_536,
            max_response_bytes=1_048_576,
        )
        formal_api = None
        if args.formal_credentials_file is not None:
            formal_credentials = config.resolve_credentials(
                load_paper_credentials(args.formal_credentials_file, config)
            )
            formal_api = PaperOrderHttp(
                config,
                formal_credentials,
                scope.formal_account_ref,
                timeout_seconds=5,
                max_request_bytes=65_536,
                max_response_bytes=1_048_576,
            )
        report = harness.recover(
            api,
            ReconciliationPolicy(timedelta(seconds=60), timedelta(seconds=30), 20),
            formal_api=formal_api,
        )
        print(json.dumps(report, sort_keys=True))
    except Exception as exc:
        print(
            json.dumps(
                {
                    "status": "FAILED",
                    "error_class": type(exc).__name__,
                    "runtime_authorized": False,
                }
            )
        )
        parser.exit(1, "engineering recovery failed; private details not logged\n")
    finally:
        if harness is not None:
            harness.close()


if __name__ == "__main__":
    main()
