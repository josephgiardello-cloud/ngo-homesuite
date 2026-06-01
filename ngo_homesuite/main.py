"""Thin compatibility entrypoint for NGO HomeSuite.

This module intentionally delegates runtime concerns to the application factory stack
(`ngo_homesuite.app_factory`, `ngo_homesuite.app.bootstrap`, and `ngo_homesuite.wsgi`).
It is kept for backward compatibility with existing commands such as
`python -m ngo_homesuite.main`.
"""

from __future__ import annotations

import argparse
import os
import sys
import unittest

from ngo_homesuite.config import get_runtime_settings


def create_app(*, compat_mode: bool = False):
    """Create the Flask application through the canonical runtime spine."""
    settings = get_runtime_settings()
    if compat_mode and not bool(getattr(settings, "allow_compat_mode", False)):
        raise RuntimeError("Non-standard entrypoint blocked")
    if compat_mode:
        legacy_fallback_enabled = os.getenv("LEGACY_FALLBACK_ENABLED", "0").strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
        }
        if not legacy_fallback_enabled:
            raise RuntimeError("Compat mode requires LEGACY_FALLBACK_ENABLED=true")
        print(
            "[COMPAT][WARN] Running in compat mode with LEGACY_FALLBACK_ENABLED=true.",
            file=sys.stderr,
        )

    from ngo_homesuite.app_factory import create_app as _create_app

    return _create_app()


def run_cli(argv: list[str] | None = None) -> None:
    """Compatibility CLI entrypoint.

    The legacy CLI menu was removed in favor of explicit web/API runtime paths.
    This command now validates app wiring by constructing the Flask app once.
    """
    _ = argv  # reserved for future CLI arguments
    create_app()
    print("CLI compatibility check completed. Use --web to run the server.")


def run_web() -> None:
    """Run the web server via bootstrap."""
    from ngo_homesuite.config import get_runtime_settings

    settings = get_runtime_settings()
    app = create_app(compat_mode=False)
    debug_default = settings.flask_env == "development"
    debug = settings.flask_debug or debug_default
    app.run(host=settings.host, port=settings.port, debug=debug)


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
