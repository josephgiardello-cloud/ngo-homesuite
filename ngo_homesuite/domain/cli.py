from __future__ import annotations

from .. import config
from .services import Actor, Services
from .store import DomainError, TenantStore


def cli_services() -> Services:
    return Services(TenantStore({config.TENANT: config.DB_PATH}))


def cli_actor() -> Actor:
    from ..auth import session

    if session.CURRENT_USER is None:
        raise DomainError("Authentication required", 401)
    return Actor(config.TENANT, int(session.CURRENT_USER["id"]))
