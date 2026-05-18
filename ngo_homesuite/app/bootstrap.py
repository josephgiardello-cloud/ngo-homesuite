from __future__ import annotations

from ngo_homesuite.config import get_runtime_settings
from ngo_homesuite.main import create_app


def _assert_not_production_entrypoint() -> None:
    settings = get_runtime_settings()
    if str(settings.flask_env).strip().lower() == "production":
        raise RuntimeError("bootstrap.py is disabled in production")


def run_server() -> None:
    _assert_not_production_entrypoint()
    app = create_app(compat_mode=True)
    settings = get_runtime_settings()
    debug_default = settings.flask_env == "development"
    debug = settings.flask_debug or debug_default
    app.run(host=settings.host, port=settings.port, debug=debug)
