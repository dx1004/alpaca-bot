"""Interactively save paper credentials in WSL home with mode 0600."""

import argparse
import getpass
import sys
from pathlib import Path

from alpaca_ross_bot.config import ConfigError
from alpaca_ross_bot.local_credentials import save_paper_credentials

DEFAULT_PATH = Path.home() / ".local/share/alpaca-bot/paper.json"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--replace", action="store_true")
    args = parser.parse_args()
    if not sys.stdin.isatty():
        parser.exit(1, "interactive terminal required; credentials were not read\n")
    key_id = getpass.getpass("Alpaca PAPER Key ID: ")
    secret_key = getpass.getpass("Alpaca PAPER Secret Key: ")
    try:
        save_paper_credentials(DEFAULT_PATH, key_id, secret_key, replace=args.replace)
    except ConfigError as exc:
        parser.exit(1, f"{exc}\n")
    print(f"Saved paper credentials to {DEFAULT_PATH} (contents hidden).")


if __name__ == "__main__":
    main()
