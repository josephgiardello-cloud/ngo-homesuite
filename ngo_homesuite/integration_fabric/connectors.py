from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass
class ConnectorResult:
    ok: bool
    message: str


class Connector(Protocol):
    name: str

    def send(self, payload: dict) -> ConnectorResult:
        ...


class ConnectorRegistry:
    """Registry for payment, messaging, registry, spreadsheet and reporting adapters."""

    def __init__(self) -> None:
        self._connectors: dict[str, Connector] = {}

    def register(self, connector: Connector) -> None:
        self._connectors[connector.name] = connector

    def names(self) -> list[str]:
        return sorted(self._connectors.keys())

    def get(self, name: str) -> Connector | None:
        return self._connectors.get(name)
