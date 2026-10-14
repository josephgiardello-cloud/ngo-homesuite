from __future__ import annotations

import os

from fastapi import FastAPI

from .api import create_app
from .config import load_store


def app_factory() -> FastAPI:
    manifest = os.environ.get("NGOHS_TENANTS_FILE")
    if not manifest:
        raise ValueError("Set NGOHS_TENANTS_FILE to a trusted organization database manifest")
    return create_app(
        load_store(manifest), secure_cookies=os.environ.get("NGOHS_INSECURE_LOCAL") != "1",
        allowed_hosts=os.environ.get("NGOHS_ALLOWED_HOSTS", "localhost,127.0.0.1").split(","),
    )
