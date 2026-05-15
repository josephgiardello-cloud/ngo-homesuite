from __future__ import annotations

from dataclasses import asdict
from typing import Iterable

from .entities import BaseEntity


class DomainRegistry:
    """In-memory registry for first-class NGO domain entities."""

    def __init__(self) -> None:
        self._entities: dict[str, BaseEntity] = {}

    def upsert(self, entity: BaseEntity) -> None:
        self._entities[entity.entity_id] = entity

    def get(self, entity_id: str) -> BaseEntity | None:
        return self._entities.get(entity_id)

    def all(self) -> Iterable[BaseEntity]:
        return self._entities.values()

    def link(self, source_id: str, relation: str, target_id: str, actor: str) -> bool:
        source = self._entities.get(source_id)
        target = self._entities.get(target_id)
        if source is None or target is None:
            return False
        source.link(relation=relation, target_id=target_id, actor=actor)
        return True

    def snapshot(self) -> dict[str, dict]:
        return {entity_id: asdict(entity) for entity_id, entity in self._entities.items()}
