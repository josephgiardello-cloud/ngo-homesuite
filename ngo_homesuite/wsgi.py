from __future__ import annotations

from ngo_homesuite.config import get_runtime_settings
from ngo_homesuite.main import create_app

if str(get_runtime_settings().flask_env).strip().lower() == "production":
	raise RuntimeError("wsgi.py is disabled in production")

app = create_app(compat_mode=True)
