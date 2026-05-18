from __future__ import annotations

from ngo_homesuite.config import get_runtime_settings
from ngo_homesuite.main import create_app


def run_server() -> None:
    app = create_app(compat_mode=True)
    settings = get_runtime_settings()
    debug_default = settings.flask_env == "development"
    debug = settings.flask_debug or debug_default
    app.run(host=settings.host, port=settings.port, debug=debug)
