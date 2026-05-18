"""NGO HomeSuite package."""

from __future__ import annotations

import os
from pathlib import Path


def _load_local_env_file() -> None:
    """Best-effort .env loader for local runs.

    This keeps `python -m ngo_homesuite.main` aligned with typical Flask `.env`
    behavior without requiring an external dotenv dependency.
    Existing process environment variables always take precedence.
    """
    env_path = Path(__file__).resolve().parents[1] / ".env"
    if not env_path.exists():
        return

    try:
        for raw_line in env_path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            if not key or key in os.environ:
                continue
            os.environ[key] = value.strip()
    except Exception:
        # Never block application startup on local env parsing.
        return


_load_local_env_file()
