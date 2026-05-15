from __future__ import annotations

import os

from ngo_homesuite.app.api import build_api_app


def run_server() -> None:
    app = build_api_app()
    host = os.environ.get("HOST", "127.0.0.1")
    port = int(os.environ.get("PORT", 5000))
    debug = os.environ.get("FLASK_DEBUG", "True").lower() == "true"
    app.run(host=host, port=port, debug=debug)
