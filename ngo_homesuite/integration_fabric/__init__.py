"""Integration fabric abstractions and adapters."""

from .connectors import Connector, ConnectorResult, ConnectorRegistry

__all__ = ["Connector", "ConnectorResult", "ConnectorRegistry"]
