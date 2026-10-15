"""Save an EODHD token through a hidden local prompt, without network access."""

import argparse
import getpass
import sys
import warnings

from alpaca_ross_bot.config import ConfigError
from alpaca_ross_bot.eodhd_credentials import (
    DEFAULT_PATH,
    load_eodhd_token,
    save_eodhd_token,
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    actions = parser.add_mutually_exclusive_group()
    actions.add_argument("--replace", action="store_true")
    actions.add_argument("--check", action="store_true")
    args = parser.parse_args()
    if args.check:
        try:
            load_eodhd_token(DEFAULT_PATH)
        except ConfigError:
            parser.exit(
                1, "Stored EODHD token could not be validated (contents hidden).\n"
            )
        print(
            "Stored EODHD token has private storage and valid local format "
            "(contents hidden; provider access not checked)."
        )
        return
    if not sys.stdin.isatty():
        parser.exit(1, "Interactive terminal required; token was not read.\n")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", getpass.GetPassWarning)
            # RTK captures stderr; default getpass uses the controlling terminal.
            token = getpass.getpass("EODHD API token (hidden): ")
        save_eodhd_token(DEFAULT_PATH, token, replace=args.replace)
    except (getpass.GetPassWarning, EOFError, KeyboardInterrupt):
        parser.exit(1, "Hidden input unavailable or cancelled; token was not saved.\n")
    except ConfigError:
        parser.exit(1, "EODHD token could not be saved safely; contents hidden.\n")
    print(f"Saved EODHD token to {DEFAULT_PATH} (contents hidden).")


if __name__ == "__main__":
    main()
