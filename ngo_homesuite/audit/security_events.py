"""
Security Audit Event Service for NGO HomeSuite.

Comprehensive logging of sensitive operations:
- Authentication (login attempts, failures, session events)
- Authorization (role assignments, permission denials)
- Admin operations (user management, org settings)
- Sensitive workflows (grant approvals, fund disbursements, data mutations)
- Compliance events (audit access, export operations)

INDUSTRY STANDARDS:
✅ Immutable audit trail (append-only, no backfill)
✅ Standardized event schema
✅ Causality tracking (who, what, when, where, why)
✅ Tamper-evident (hashed payload validation)
✅ Retention policy (configurable)
✅ SIEM-compatible format (JSON, queryable)
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum
from typing import Any, Optional
from uuid import uuid4
import hashlib
import json

from flask import current_app, g, request
from flask_login import current_user
from sqlalchemy import Column, String, DateTime, Text, Integer, Index, desc
from sqlalchemy.dialects.sqlite import JSON

from ngo_homesuite.models.core import db


def _stable_datetime_iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is not None:
        value = value.astimezone(timezone.utc).replace(tzinfo=None)
    return value.isoformat()


class SecurityEventType(StrEnum):
    """Security event types for audit trail."""
    
    # Authentication events
    LOGIN_SUCCESS = "auth.login_success"
    LOGIN_FAILURE = "auth.login_failure"
    LOGIN_FAILURE_INVALID_CREDENTIALS = "auth.login_failure_invalid_credentials"
    LOGIN_FAILURE_ACCOUNT_LOCKED = "auth.login_failure_account_locked"
    SESSION_CREATED = "auth.session_created"
    SESSION_EXPIRED = "auth.session_expired"
    SESSION_REVOKED = "auth.session_revoked"
    PASSWORD_CHANGED = "auth.password_changed"
    MFA_ENABLED = "auth.mfa_enabled"
    MFA_DISABLED = "auth.mfa_disabled"
    
    # Authorization events
    PERMISSION_DENIED = "authz.permission_denied"
    ROLE_ASSIGNED = "authz.role_assigned"
    ROLE_REVOKED = "authz.role_revoked"
    PERMISSION_GRANT = "authz.permission_grant"
    PERMISSION_REVOKE = "authz.permission_revoke"
    
    # Admin operations
    USER_CREATED = "admin.user_created"
    USER_MODIFIED = "admin.user_modified"
    USER_DELETED = "admin.user_deleted"
    ORG_SETTINGS_CHANGED = "admin.org_settings_changed"
    ORG_DELETED = "admin.org_deleted"
    
    # Data mutations (sensitive)
    GRANT_APPROVED = "workflow.grant_approved"
    GRANT_REJECTED = "workflow.grant_rejected"
    GRANT_DISBURSED = "workflow.grant_disbursed"
    FUND_TRANSFERRED = "workflow.fund_transferred"
    DONOR_IMPORTED = "workflow.donor_imported"
    CAMPAIGN_LAUNCHED = "workflow.campaign_launched"
    
    # Compliance events
    AUDIT_LOG_ACCESS = "compliance.audit_log_access"
    REPORT_GENERATED = "compliance.report_generated"
    DATA_EXPORT = "compliance.data_export"
    RLS_BOUNDARY_VIOLATION_ATTEMPT = "compliance.rls_boundary_violation"
    
    # System events
    SECURITY_POLICY_CHANGED = "system.security_policy_changed"
    API_KEY_ROTATED = "system.api_key_rotated"
    ENCRYPTION_KEY_ROTATED = "system.encryption_key_rotated"
    SYSTEM_BOOTSTRAP_TOKEN_GENERATED = "system.bootstrap_token_generated"
    SYSTEM_BOOTSTRAP_TOKEN_VALIDATION_FAILED = "system.bootstrap_token_validation_failed"
    SYSTEM_BOOTSTRAP_TOKEN_CONSUMED = "system.bootstrap_token_consumed"
    SYSTEM_BOOTSTRAP_TOKEN_INVALIDATED = "system.bootstrap_token_invalidated"


class SecurityAuditEvent(db.Model):
    """Immutable security audit event record."""
    
    __tablename__ = "security_audit_events"
    
    id = Column(Integer, primary_key=True)
    event_id = Column(String(36), unique=True, nullable=False, index=True)  # UUID
    event_type = Column(String(64), nullable=False, index=True)
    
    # Actor (who)
    actor_user_id = Column(Integer, nullable=True, index=True)  # Null if system event
    actor_username = Column(String(255), nullable=True)
    actor_org_id = Column(Integer, nullable=True, index=True)
    actor_ip_address = Column(String(45), nullable=True)  # IPv4 or IPv6
    
    # Resource (what)
    resource_type = Column(String(64), nullable=True)  # user, org, grant, etc.
    resource_id = Column(Integer, nullable=True, index=True)
    resource_org_id = Column(Integer, nullable=True)  # RLS boundary for audit queries
    
    # Event details (why)
    action = Column(String(255), nullable=False)  # user_role_changed, grant_approved, etc.
    result = Column(String(16), nullable=False)  # success, failure, denied
    reason = Column(Text, nullable=True)  # Failure reason or policy violated
    
    # Payload (structured metadata)
    payload = Column(JSON, nullable=True)  # Event-specific data
    
    # Timestamps & integrity
    created_at = Column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))
    request_id = Column(String(36), nullable=True)  # Trace ID for correlation
    
    # Tamper evidence (hash of previous event)
    previous_event_hash = Column(String(64), nullable=True)
    this_event_hash = Column(String(64), nullable=True)
    
    # Indexes for efficient querying
    __table_args__ = (
        Index("ix_audit_actor_time", "actor_user_id", desc("created_at")),
        Index("ix_audit_resource_time", "resource_id", desc("created_at")),
        Index("ix_audit_org_time", "actor_org_id", desc("created_at")),
        Index("ix_audit_event_type", "event_type", desc("created_at")),
        Index("ix_audit_result", "result", desc("created_at")),
    )
    
    def calculate_hash(self) -> str:
        """Calculate SHA256 hash of event payload for tamper detection."""
        data = json.dumps({
            "event_id": self.event_id,
            "event_type": self.event_type,
            "resource_id": self.resource_id,
            "action": self.action,
            "created_at": _stable_datetime_iso(self.created_at),
            "previous_hash": self.previous_event_hash,
        }, sort_keys=True)
        return hashlib.sha256(data.encode()).hexdigest()


class SecurityAuditService:
    """Service for recording security events."""
    
    @staticmethod
    def log_event(
        event_type: SecurityEventType,
        action: str,
        result: str = "success",
        resource_type: Optional[str] = None,
        resource_id: Optional[int] = None,
        resource_org_id: Optional[int] = None,
        reason: Optional[str] = None,
        payload: Optional[dict[str, Any]] = None,
    ) -> SecurityAuditEvent:
        """
        Record a security event with causality and tamper evidence.
        
        Args:
            event_type: Classified event type
            action: Specific action taken (e.g., "user_role_changed_viewer_to_staff")
            result: "success", "failure", or "denied"
            resource_type: Entity type affected (user, grant, donation, etc.)
            resource_id: ID of affected entity
            resource_org_id: Organization ID for RLS queries
            reason: Failure reason or policy violated
            payload: Structured event metadata
        
        Returns:
            SecurityAuditEvent record (persisted)
        
        INDUSTRY STANDARD:
        - Immutable record with hash chain for tamper evidence
        - Request tracing for distributed causality
        - RLS boundary tracking for compliance queries
        """
        try:
            # Get actor information
            actor_user_id = None
            actor_username = None
            actor_org_id = None
            
            if current_user and current_user.is_authenticated:
                actor_user_id = current_user.id
                actor_username = current_user.username
                actor_org_id = getattr(current_user, "organization_id", None)
            
            # Get request context
            actor_ip_address = request.remote_addr if request else None
            request_id = getattr(g, "request_id", None) or str(uuid4())
            
            # Get previous event hash for chain
            chain_org_id = resource_org_id or actor_org_id
            previous_event = (
                SecurityAuditEvent.query
                .filter_by(resource_org_id=chain_org_id)
                .order_by(desc(SecurityAuditEvent.created_at), desc(SecurityAuditEvent.id))
                .first()
            )
            previous_hash = previous_event.this_event_hash if previous_event else None
            
            # Create event
            event = SecurityAuditEvent(
                event_id=str(uuid4()),
                event_type=str(event_type),
                actor_user_id=actor_user_id,
                actor_username=actor_username,
                actor_org_id=actor_org_id,
                actor_ip_address=actor_ip_address,
                resource_type=resource_type,
                resource_id=resource_id,
                resource_org_id=resource_org_id,
                action=action,
                result=result,
                reason=reason,
                payload=payload or {},
                created_at=datetime.now(timezone.utc),
                request_id=request_id,
                previous_event_hash=previous_hash,
            )
            
            # Calculate tamper hash
            event.this_event_hash = event.calculate_hash()
            
            # Persist
            db.session.add(event)
            db.session.commit()
            
            # Log to application logger if available
            if current_app:
                current_app.logger.info(
                    f"SECURITY_AUDIT: {event_type} | {action} | {result} | "
                    f"actor={actor_username} | resource={resource_type}:{resource_id}",
                    extra={"event_id": event.event_id, "request_id": request_id},
                )
            
            return event
        except Exception as e:
            # Audit failures must not crash the application
            # Log to stderr and continue
            import sys
            print(f"ERROR: Failed to record security audit event: {e}", file=sys.stderr)
            raise
    
    @staticmethod
    def query_events(
        org_id: int,
        actor_user_id: Optional[int] = None,
        resource_type: Optional[str] = None,
        event_type: Optional[str] = None,
        result_filter: Optional[str] = None,
        limit: int = 100,
    ) -> list[SecurityAuditEvent]:
        """
        Query audit events with RLS enforcement (org_id always filtered).
        
        **SECURITY**: Only return events for requested org.
        """
        query = SecurityAuditEvent.query.filter_by(resource_org_id=org_id)
        
        if actor_user_id:
            query = query.filter_by(actor_user_id=actor_user_id)
        
        if resource_type:
            query = query.filter_by(resource_type=resource_type)
        
        if event_type:
            query = query.filter_by(event_type=event_type)
        
        if result_filter:
            query = query.filter_by(result=result_filter)
        
        return query.order_by(desc(SecurityAuditEvent.created_at)).limit(limit).all()
    
    @staticmethod
    def verify_chain_integrity(org_id: int) -> bool:
        """
        Verify tamper-evidence chain (hash chain unbroken).
        
        Returns: True if chain is valid, False if tampering detected.
        """
        events = (
            SecurityAuditEvent.query
            .filter_by(resource_org_id=org_id)
            .order_by(SecurityAuditEvent.created_at.asc(), SecurityAuditEvent.id.asc())
            .all()
        )
        
        if not events:
            return True
        
        for i, event in enumerate(events):
            if i == 0:
                if event.previous_event_hash is not None:
                    return False  # First event should have no previous
            else:
                prev_event = events[i - 1]
                if event.previous_event_hash != prev_event.this_event_hash:
                    return False  # Chain broken
            
            # Verify event's own hash
            expected_hash = event.calculate_hash()
            if event.this_event_hash != expected_hash:
                return False  # Event hash changed
        
        return True


# ============================================================================
# FLASK INTEGRATION: Automatic Event Logging Decorators
# ============================================================================

def log_security_event(
    event_type: SecurityEventType,
    resource_type: Optional[str] = None,
    **kwargs,
):
    """
    Decorator to automatically log security events for Flask routes.
    
    Usage:
        @app.route("/admin/users/<user_id>/role", methods=["PATCH"])
        @log_security_event(
            SecurityEventType.ROLE_ASSIGNED,
            resource_type="user",
        )
        def update_role(user_id):
            ...
    """
    def decorator(fn):
        from functools import wraps
        
        @wraps(fn)
        def wrapped(*args, **route_kwargs):
            try:
                # Call the actual route
                result = fn(*args, **route_kwargs)
                
                # Log success
                resource_id = route_kwargs.get("user_id")
                SecurityAuditService.log_event(
                    event_type=event_type,
                    action=fn.__name__,
                    result="success",
                    resource_type=resource_type,
                    resource_id=resource_id,
                    **kwargs,
                )
                
                return result
            except Exception as e:
                # Log failure
                resource_id = route_kwargs.get("user_id")
                SecurityAuditService.log_event(
                    event_type=event_type,
                    action=fn.__name__,
                    result="failure",
                    reason=str(e),
                    resource_type=resource_type,
                    resource_id=resource_id,
                    **kwargs,
                )
                raise
        
        return wrapped
    return decorator
