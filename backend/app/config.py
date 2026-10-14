from __future__ import annotations

import json
import re
from pathlib import Path

from ngo_homesuite.domain.store import TenantStore


def load_store(manifest: str | Path) -> TenantStore:
    path = Path(manifest).resolve()
    databases = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(databases, dict) or not databases:
        raise ValueError("Tenant manifest must be a nonempty object mapping organization names to paths")
    resolved: dict[str, Path] = {}
    for tenant, database in databases.items():
        if not re.fullmatch(r"[a-z0-9_-]{1,40}", tenant) or not isinstance(database, str) or not database:
            raise ValueError("Invalid organization name or database path")
        resolved[tenant] = path.parent / database
    return TenantStore(resolved)
