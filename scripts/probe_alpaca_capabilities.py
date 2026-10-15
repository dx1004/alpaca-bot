"""Run redacted, read-only Alpaca capability checks."""

import argparse
import json
import os
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

from alpaca_ross_bot.capabilities import ProbeStatus, probe_rest, probe_streams
from alpaca_ross_bot.config import PaperConfig
from alpaca_ross_bot.local_credentials import load_paper_credentials


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--config", type=Path, default=Path("config/paper.example.toml")
    )
    parser.add_argument(
        "--connect",
        action="store_true",
        help="Send read-only requests to the configured Alpaca paper/data endpoints",
    )
    parser.add_argument(
        "--sip-screeners-only",
        action="store_true",
        help=(
            "Check only Alpaca's two SIP-based screener endpoints; "
            "skip account, feed, and news checks"
        ),
    )
    parser.add_argument(
        "--credentials-file",
        type=Path,
        help="Local mode-0600 paper credential file from the setup script",
    )
    args = parser.parse_args()
    config = PaperConfig.from_toml(args.config)
    environment = (
        load_paper_credentials(args.credentials_file, config)
        if args.credentials_file is not None
        else os.environ
    )
    report = probe_rest(
        config,
        environment,
        datetime.now(UTC).date(),
        connect=args.connect,
        only_checks=(
            frozenset({"screener_most_actives_sip", "screener_movers_sip"})
            if args.sip_screeners_only
            else None
        ),
    )
    stream_checks = (
        ()
        if args.sip_screeners_only
        else probe_streams(config, environment, connect=args.connect)
    )
    report = replace(
        report,
        stream_checks=stream_checks,
        websocket_access_verified=bool(stream_checks)
        and all(check.status is ProbeStatus.REACHABLE for check in stream_checks),
    )
    print(json.dumps(report.as_dict(), sort_keys=True))


if __name__ == "__main__":
    main()
