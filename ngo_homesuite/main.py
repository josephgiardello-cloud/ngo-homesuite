"""Thin compatibility entrypoint for NGO HomeSuite.

This module intentionally delegates runtime concerns to the application factory stack
(`ngo_homesuite.app_factory`, `ngo_homesuite.app.bootstrap`, and `ngo_homesuite.wsgi`).
It is kept for backward compatibility with existing commands such as
`python -m ngo_homesuite.main` and `python NGOMG.py`.
"""

from __future__ import annotations

import argparse
import sys
import unittest


def run_cli(argv: list[str] | None = None) -> None:
    """Compatibility CLI entrypoint.

    The legacy CLI menu was removed in favor of explicit web/API runtime paths.
    This command now validates app wiring by constructing the Flask app once.
    """
    from ngo_homesuite.app_factory import create_app

    _ = argv  # reserved for future CLI arguments
    create_app()
    print("CLI compatibility check completed. Use --web to run the server.")


def run_web() -> None:
    """Run the web server via bootstrap."""
    from ngo_homesuite.app.bootstrap import run_server

    run_server()


def run_tests() -> None:
    """Run legacy unittest-discovery mode."""
    unittest.main(module=None)


def main(argv: list[str] | None = None) -> None:
    """Dispatch to web/cli/test modes."""
    argv = list(argv if argv is not None else sys.argv[1:])

    parser = argparse.ArgumentParser(description="NGO HomeSuite entrypoint")
    parser.add_argument("--web", action="store_true", help="Run the web server (default)")
    parser.add_argument("--cli", action="store_true", help="Run compatibility CLI checks")
    parser.add_argument("--test", action="store_true", help="Run unittest discovery")
    args, _unknown = parser.parse_known_args(argv)

    if args.test:
        run_tests()
        return
    if args.cli:
        run_cli(argv)
        return
    run_web()


if __name__ == "__main__":
    main()
