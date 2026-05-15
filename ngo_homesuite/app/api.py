from __future__ import annotations

from flask import Flask

from ngo_homesuite.app_factory import create_app


def build_api_app() -> Flask:
    """Build the API/web app with V2 runtime components attached."""
    return create_app()
