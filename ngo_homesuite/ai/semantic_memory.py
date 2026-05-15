from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
from typing import Any

from ngo_homesuite.domain import DomainRegistry


class SemanticMemoryLayer:
    """Simple semantic memory over the domain entity graph."""

    def __init__(self) -> None:
        self._nodes: dict[str, dict[str, Any]] = {}

    @staticmethod
    def _tokenize(text: str) -> set[str]:
        return {chunk for chunk in text.lower().replace("_", " ").split() if chunk}

    def index_registry(self, registry: DomainRegistry) -> None:
        now = datetime.now(timezone.utc).isoformat()
        for entity in registry.all():
            payload = asdict(entity)
            payload["indexed_at"] = now
            payload["entity_type"] = type(entity).__name__
            self._nodes[entity.entity_id] = payload

    def _score(self, query_tokens: set[str], node: dict[str, Any]) -> float:
        haystack = " ".join(
            [
                str(node.get("name", "")),
                str(node.get("entity_type", "")),
                str(node.get("lifecycle_state", "")),
                " ".join(node.get("relationships", {}).keys()),
            ]
        )
        node_tokens = self._tokenize(haystack)
        overlap = len(query_tokens & node_tokens)
        relation_bonus = len(node.get("relationships", {})) * 0.1
        return overlap + relation_bonus

    def retrieve(self, query: str, limit: int = 5) -> list[dict[str, Any]]:
        query_tokens = self._tokenize(query)
        scored: list[tuple[float, dict[str, Any]]] = []
        for node in self._nodes.values():
            score = self._score(query_tokens, node)
            if score > 0:
                scored.append((score, node))
        scored.sort(key=lambda item: item[0], reverse=True)
        return [node for _, node in scored[:limit]]

    def assemble_context(self, task: str, limit: int = 5) -> dict[str, Any]:
        top_nodes = self.retrieve(task, limit=limit)
        return {
            "task": task,
            "retrieved_entities": top_nodes,
            "entity_count": len(top_nodes),
        }
