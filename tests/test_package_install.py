"""Baseline check that the project is installed into its virtual environment."""

from importlib.metadata import version

import alpaca_ross_bot


def test_package_install() -> None:
    assert alpaca_ross_bot.__doc__
    assert version("alpaca-ross-bot") == "0.1.0"
