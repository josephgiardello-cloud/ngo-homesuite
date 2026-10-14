from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest

from ngo_homesuite.db.schema import init_db
from ngo_homesuite.domain.identity import Identity
from ngo_homesuite.domain.services import Actor, Services
from ngo_homesuite.domain.store import TenantStore

TEST_PASSWORD = "fixture-only-password"  # pragma: allowlist secret


@dataclass
class System:
    store: TenantStore
    services: Services
    identity: Identity
    admin: Actor
    payer: Actor
    fundraiser: Actor
    viewer: Actor
    outsider: Actor
    paths: dict[str, Path]


def build_system(directory: Path) -> System:
    paths = {tenant: directory / f"{tenant}.db" for tenant in ("alpha", "beta")}
    for path in paths.values():
        init_db(str(path))
    store = TenantStore(paths)
    services = Services(store)
    identity = Identity(services)
    admin = Actor("alpha", identity.create_user("alpha", "admin", TEST_PASSWORD, "admin"))
    payer = Actor("alpha", identity.create_user("alpha", "payer", TEST_PASSWORD, "admin", actor=admin))
    fundraiser = Actor("alpha", identity.create_user("alpha", "fundraiser", TEST_PASSWORD, "fundraiser", actor=admin))
    viewer = Actor("alpha", identity.create_user("alpha", "viewer", TEST_PASSWORD, "viewer", actor=admin))
    outsider = Actor("beta", identity.create_user("beta", "admin", TEST_PASSWORD, "admin"))
    return System(store, services, identity, admin, payer, fundraiser, viewer, outsider, paths)


@pytest.fixture
def system(tmp_path: Path) -> System:
    return build_system(tmp_path)
