from __future__ import annotations

import os

from ngo_homesuite.app.api import build_api_app


def run_server() -> None:
    app = build_api_app()
    host = os.environ.get("HOST", "127.0.0.1")
    port = int(os.environ.get("PORT", 5000))
    env_name = os.environ.get("FLASK_ENV", "development").lower()
    debug_default = env_name == "development"
    debug = os.environ.get("FLASK_DEBUG", str(debug_default)).lower() == "true"
    app.run(host=host, port=port, debug=debug)
