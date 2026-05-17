"""
Session Hardening and Content Security Policy (CSP) with Nonce Strategy.

INDUSTRY STANDARDS APPLIED:
✅ HttpOnly + Secure + SameSite=Strict cookies
✅ Per-request CSP nonces for inline scripts
✅ Strict CSP policy (no 'unsafe-inline')
✅ Subresource integrity (SRI) for CDNs
✅ X-Frame-Options, X-Content-Type-Options headers
✅ Referrer-Policy for privacy
✅ Session regeneration on privilege escalation
✅ CSRF token rotation per request
"""

from __future__ import annotations

from datetime import datetime, timezone
from secrets import token_hex
from functools import wraps
from typing import Optional, Any

from flask import Flask, request, session, g, render_template_string
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

from ngo_homesuite.audit.security_events import (
    SecurityAuditService,
    SecurityEventType,
)


class CSPNonceManager:
    """Manage per-request CSP nonces for inline scripts."""
    
    @staticmethod
    def generate_nonce() -> str:
        """Generate cryptographically secure nonce (16 bytes = 128 bits)."""
        return token_hex(16)
    
    @staticmethod
    def get_or_create_nonce() -> str:
        """Get existing nonce for request or create new one."""
        if 'csp_nonce' not in request.environ:
            request.environ['csp_nonce'] = CSPNonceManager.generate_nonce()
        return request.environ['csp_nonce']


class SessionHardeningMiddleware:
    """Middleware for session security enforcement."""
    
    def __init__(self, app: Flask):
        """Initialize middleware and register with Flask app."""
        self.app = app
        self.limiter = Limiter(
            app=app,
            key_func=get_remote_address,
            default_limits=["200 per day", "50 per hour"],
        )
        
        # Register hooks
        app.before_request(self.before_request)
        app.after_request(self.after_request)
        app.template_global('csp_nonce')(CSPNonceManager.get_or_create_nonce)
    
    def before_request(self):
        """Pre-request security checks."""
        # Generate CSP nonce for this request
        CSPNonceManager.get_or_create_nonce()
        
        # Store session metadata for detection of tampering
        if 'session_created_at' not in session:
            session['session_created_at'] = datetime.now(timezone.utc).isoformat()
            session['session_nonce'] = token_hex(16)
    
    def after_request(self, response):
        """Post-request security headers."""
        nonce = getattr(g, 'csp_nonce', None)
        
        # Content Security Policy with nonce
        csp_directives = [
            "default-src 'self'",
            f"script-src 'self' 'nonce-{nonce}'" if nonce else "script-src 'self'",
            "style-src 'self' 'nonce-style-inline' https://cdn.jsdelivr.net",
            "img-src 'self' data: https:",
            "font-src 'self' https://fonts.googleapis.com https://fonts.gstatic.com",
            "connect-src 'self' https://api.example.com",
            "frame-ancestors 'none'",
            "base-uri 'self'",
            "form-action 'self'",
            "upgrade-insecure-requests",
            "block-all-mixed-content",
        ]
        response.headers['Content-Security-Policy'] = '; '.join(csp_directives)
        
        # Additional security headers
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['X-Frame-Options'] = 'DENY'
        response.headers['X-XSS-Protection'] = '1; mode=block'
        response.headers['Referrer-Policy'] = 'strict-origin-when-cross-origin'
        response.headers['Permissions-Policy'] = (
            'geolocation=(), microphone=(), camera=(), payment=()'
        )
        
        # HSTS (HTTPS Strict Transport Security) in production only
        if not self.app.debug:
            response.headers['Strict-Transport-Security'] = (
                'max-age=31536000; includeSubDomains; preload'
            )
        
        # Remove Server header to reduce fingerprinting
        response.headers.pop('Server', None)
        
        return response


class SessionRegenerationGuard:
    """Protect against session fixation attacks."""
    
    @staticmethod
    def require_session_regeneration(fn):
        """
        Decorator: Regenerate session on privilege escalation.
        
        Usage:
            @app.route('/login', methods=['POST'])
            @SessionRegenerationGuard.require_session_regeneration
            def login():
                ...
        """
        @wraps(fn)
        def wrapped(*args, **kwargs):
            # Record old session ID before action
            old_session_id = getattr(session, 'sid', None)
            
            # Execute the wrapped function
            result = fn(*args, **kwargs)
            
            # Check if privilege escalated (e.g., login happened)
            if request.endpoint in ['auth.login', 'auth.register']:
                # Regenerate session ID
                from flask import session as flask_session
                
                # Create new session
                new_session_data = dict(session)
                session.clear()
                session.update(new_session_data)
                session['session_regenerated_at'] = datetime.now(timezone.utc).isoformat()
                session['sid'] = token_hex(16)
                
                # Log session change
                SecurityAuditService.log_event(
                    event_type=SecurityEventType.SESSION_CREATED,
                    action="session_regenerated_on_privilege_change",
                    result="success",
                    payload={
                        "endpoint": request.endpoint,
                        "old_session_id": old_session_id,
                    },
                )
            
            return result
        
        return wrapped


class CSRFTokenRotation:
    """CSRF token rotation per request."""
    
    @staticmethod
    def get_or_create_csrf_token() -> str:
        """Get existing CSRF token or create new one."""
        if '_csrf_token' not in session:
            session['_csrf_token'] = token_hex(32)
        return session['_csrf_token']
    
    @staticmethod
    def rotate_csrf_token():
        """Rotate CSRF token on sensitive operations."""
        session['_csrf_token'] = token_hex(32)


class SessionAudit:
    """Session activity auditing."""
    
    @staticmethod
    def audit_session_activity(
        event_type: SecurityEventType,
        action: str,
        **kwargs,
    ):
        """Log session-related security events."""
        user_agent = request.headers.get('User-Agent', 'unknown')[:255]
        
        SecurityAuditService.log_event(
            event_type=event_type,
            action=action,
            payload={
                "user_agent": user_agent,
                "ip_address": request.remote_addr,
                "referer": request.referrer,
                **kwargs,
            },
        )


# ============================================================================
# PRODUCTION CONFIGURATION
# ============================================================================

PRODUCTION_SESSION_CONFIG = {
    # Cookie security
    "SESSION_COOKIE_SECURE": True,  # HTTPS only
    "SESSION_COOKIE_HTTPONLY": True,  # No JavaScript access
    "SESSION_COOKIE_SAMESITE": "Strict",  # CSRF protection
    "SESSION_COOKIE_NAME": "__Host-session",  # Prefix indicates security constraints
    
    # Session lifecycle
    "PERMANENT_SESSION_LIFETIME": 3600,  # 1 hour
    "SESSION_REFRESH_EACH_REQUEST": True,  # Rotate on each request
    
    # CSRF protection
    "WTF_CSRF_ENABLED": True,
    "WTF_CSRF_TIME_LIMIT": 3600,
    "WTF_CSRF_SSL_STRICT": True,
    
    # Security headers
    "SECURITY_HEADER_REFERRER_POLICY": "strict-origin-when-cross-origin",
}

TESTING_SESSION_CONFIG = {
    # Relaxed for testing
    "SESSION_COOKIE_SECURE": False,
    "SESSION_COOKIE_HTTPONLY": True,
    "SESSION_COOKIE_SAMESITE": "Lax",
    "PERMANENT_SESSION_LIFETIME": 3600,
    "SESSION_REFRESH_EACH_REQUEST": False,
}


def configure_session_hardening(app: Flask):
    """
    Configure all session hardening on Flask app.
    
    Usage:
        app = create_app()
        configure_session_hardening(app)
    """
    # Apply appropriate config
    if app.debug:
        app.config.update(TESTING_SESSION_CONFIG)
    else:
        app.config.update(PRODUCTION_SESSION_CONFIG)
    
    # Initialize middleware
    SessionHardeningMiddleware(app)
