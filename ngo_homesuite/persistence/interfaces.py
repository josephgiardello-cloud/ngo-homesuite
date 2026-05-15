from __future__ import annotations

from typing import Any
from typing import Protocol, runtime_checkable


@runtime_checkable
class UnitOfWorkPort(Protocol):
    def commit(self) -> None: ...

    def rollback(self) -> None: ...


@runtime_checkable
class WorkflowRepositoryPort(Protocol):
    def save(self, instance: Any, *, uow: UnitOfWorkPort | None = None) -> Any: ...

    def get(
        self,
        instance_id: str,
        *,
        org_id: str | None = None,
        allow_cross_tenant: bool = False,
    ) -> Any | None: ...

    def list_for_org(self, org_id: str) -> list[Any]: ...


@runtime_checkable
class WorkflowDefinitionRepositoryPort(Protocol):
    def ensure_definition(self, definition: Any, *, allow_global_scope: bool = False) -> object: ...

    def list_active_definitions(self, *, allow_global_scope: bool = False) -> dict[str, Any]: ...
