from __future__ import annotations

from ngo_homesuite.app.api import build_api_app
from ngo_homesuite.config import get_runtime_settings


def run_server() -> None:
    app = build_api_app()
    settings = get_runtime_settings()
    debug_default = settings.flask_env == "development"
    debug = settings.flask_debug or debug_default
    app.run(host=settings.host, port=settings.port, debug=debug)
