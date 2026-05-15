"""
Dedicated alerting module for security/system events.
Extend this with email, SMS, or other integrations as needed.
"""
from ..db.utils import audit

def alert_security_event(event: str, details: dict) -> None:
    """
    Generic security/system alert. Extend as needed.
    """
    audit("security.alert", entity_type="system", details={"event": event, **details})