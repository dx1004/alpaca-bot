"""Local explicit-journal optional-news replay; no network or trading."""

import argparse
import json
from datetime import datetime
from pathlib import Path

from alpaca_ross_bot.journal import Journal
from alpaca_ross_bot.news_preference import capture_optional_news_view


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--journal", type=Path, required=True)
    parser.add_argument("--data-root", type=Path, required=True)
    parser.add_argument(
        "--repo-root", type=Path, default=Path(__file__).resolve().parents[1]
    )
    parser.add_argument("--symbol", action="append", required=True)
    parser.add_argument("--ref", type=int, action="append", default=[])
    parser.add_argument("--decision-at", required=True)
    parser.add_argument("--processed-at", required=True)
    args = parser.parse_args()
    if not args.journal.is_file():
        parser.error("journal must already exist")
    journal = Journal(args.journal, args.data_root)
    try:
        for symbol in args.symbol:
            event = capture_optional_news_view(
                journal,
                repo_root=args.repo_root,
                symbol=symbol,
                input_refs=tuple(args.ref),
                decision_at=datetime.fromisoformat(
                    args.decision_at.replace("Z", "+00:00")
                ),
                processed_at=datetime.fromisoformat(
                    args.processed_at.replace("Z", "+00:00")
                ),
            )
            row = journal.read_event(event.sequence)
            assert row is not None
            print(
                json.dumps(
                    {"sequence": event.sequence, "view": json.loads(row[1])},
                    sort_keys=True,
                )
            )
    finally:
        journal.close()


if __name__ == "__main__":
    main()
