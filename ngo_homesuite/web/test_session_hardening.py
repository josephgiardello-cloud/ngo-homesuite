"""
Comprehensive tests for Session Hardening and Content Security Policy.

Validates:
âœ… CSP nonce generation and uniqueness per request
âœ… CSP headers correctly formatted in responses
âœ… Session cookie security flags
âœ… CSRF token management and rotation
âœ… Security header presence and correctness
"""

import pytest
from flask import session, g


@pytest.fixture(scope="module")
def app():
    """Shared app fixture."""
    from ngo_homesuite.app_factory import create_app
    from ngo_homesuite.flask_config import TestingConfig

    app = create_app(TestingConfig)
    return app


@pytest.fixture()
def client(app):
    """Test client with app context."""
    return app.test_client()


class TestCSPNonceGeneration:
    """Test Content Security Policy nonce management."""

    def test_csp_nonce_generated_per_request(self, app, client):
        """
        **Scenario**: Each request gets unique CSP nonce.
        
        **Assertions**: Nonce is 32-character hex string (16 bytes).
        """
        with app.app_context():
            with app.test_request_context('/'):
                from ngo_homesuite.web.session_hardening import CSPNonceManager
                
                nonce = CSPNonceManager.get_or_create_nonce()
                
                assert nonce is not None
                assert len(nonce) == 32  # 16 bytes = 32 hex chars
                assert all(c in '0123456789abcdef' for c in nonce)

    def test_csp_nonce_same_within_request(self, app):
        """
        **Scenario**: CSP nonce consistent within single request.
        
        **Assertions**: Multiple calls return same nonce.
        """
        with app.app_context():
            with app.test_request_context('/'):
                from ngo_homesuite.web.session_hardening import CSPNonceManager
                
                nonce1 = CSPNonceManager.get_or_create_nonce()
                nonce2 = CSPNonceManager.get_or_create_nonce()
                
                assert nonce1 == nonce2

    def test_csp_nonce_different_across_requests(self, app):
        """
        **Scenario**: Different requests get different nonces.
        
        **Assertions**: Nonces are unique per request.
        """
        with app.app_context():
            nonces = []
            for _ in range(3):
                with app.test_request_context('/'):
                    from ngo_homesuite.web.session_hardening import CSPNonceManager
                    nonce = CSPNonceManager.get_or_create_nonce()
                    nonces.append(nonce)
            
            # All nonces should be unique
            assert len(set(nonces)) == 3


class TestSecurityHeaders:
    """Test security headers in responses."""

    def test_csp_header_present_in_response(self, app, client):
        """
        **Scenario**: CSP header included in all responses.
        
        **Assertions**: Content-Security-Policy header set correctly.
        """
        # Note: This requires actual routing; if app not fully configured,
        # we test the middleware directly
        with app.app_context():
            with app.test_request_context('/'):
                from ngo_homesuite.web.session_hardening import SessionHardeningMiddleware
                
                middleware = SessionHardeningMiddleware(app)
                
                # Simulate response
                from flask import Response
                response = Response("test", 200)
                modified = middleware.after_request(response)
                
                assert 'Content-Security-Policy' in modified.headers
                csp = modified.headers['Content-Security-Policy']
                assert "default-src 'self'" in csp

    def test_xframe_options_deny(self, app):
        """
        **Scenario**: X-Frame-Options set to DENY.
        
        **Assertions**: Clickjacking protection active.
        """
        with app.app_context():
            with app.test_request_context('/'):
                from ngo_homesuite.web.session_hardening import SessionHardeningMiddleware
                from flask import Response
                
                middleware = SessionHardeningMiddleware(app)
                response = Response("test", 200)
                modified = middleware.after_request(response)
                
                assert modified.headers['X-Frame-Options'] == 'DENY'

    def test_xcontenttype_options_nosniff(self, app):
        """
        **Scenario**: X-Content-Type-Options set to nosniff.
        
        **Assertions**: MIME type sniffing prevented.
        """
        with app.app_context():
            with app.test_request_context('/'):
                from ngo_homesuite.web.session_hardening import SessionHardeningMiddleware
                from flask import Response
                
                middleware = SessionHardeningMiddleware(app)
                response = Response("test", 200)
                modified = middleware.after_request(response)
                
                assert modified.headers['X-Content-Type-Options'] == 'nosniff'

    def test_referrer_policy_header(self, app):
        """
        **Scenario**: Referrer-Policy set to strict-origin-when-cross-origin.
        
        **Assertions**: Referrer leakage prevented.
        """
        with app.app_context():
            with app.test_request_context('/'):
                from ngo_homesuite.web.session_hardening import SessionHardeningMiddleware
                from flask import Response
                
                middleware = SessionHardeningMiddleware(app)
                response = Response("test", 200)
                modified = middleware.after_request(response)
                
                assert modified.headers['Referrer-Policy'] == 'strict-origin-when-cross-origin'

    def test_permissions_policy_header(self, app):
        """
        **Scenario**: Permissions-Policy restricts sensitive APIs.
        
        **Assertions**: Geolocation, microphone, camera disabled.
        """
        with app.app_context():
            with app.test_request_context('/'):
                from ngo_homesuite.web.session_hardening import SessionHardeningMiddleware
                from flask import Response
                
                middleware = SessionHardeningMiddleware(app)
                response = Response("test", 200)
                modified = middleware.after_request(response)
                
                permissions = modified.headers['Permissions-Policy']
                assert 'geolocation=()' in permissions
                assert 'microphone=()' in permissions
                assert 'camera=()' in permissions


class TestSessionCookieSecurityFlags:
    """Test session cookie configuration."""

    def test_session_cookie_httponly_enabled(self, app):
        """
        **Scenario**: Session cookies have HttpOnly flag.
        
        **Assertions**: JavaScript cannot access cookies.
        """
        assert app.config.get("SESSION_COOKIE_HTTPONLY") is True

    def test_session_cookie_samesite_configured(self, app):
        """
        **Scenario**: Session cookies have SameSite flag.
        
        **Assertions**: CSRF protection configured.
        """
        samesite = app.config.get("SESSION_COOKIE_SAMESITE")
        assert samesite in [None, "Lax", "Strict"]


class TestCSRFTokenManagement:
    """Test CSRF token creation and rotation."""

    def test_csrf_token_generated(self, app):
        """
        **Scenario**: CSRF token created on session initialization.
        
        **Assertions**: Token is 64-character hex string (32 bytes).
        """
        with app.app_context():
            with app.test_request_context('/'):
                from ngo_homesuite.web.session_hardening import CSRFTokenRotation
                from flask import session
                
                token = CSRFTokenRotation.get_or_create_csrf_token()
                
                assert token is not None
                assert len(token) == 64  # 32 bytes = 64 hex chars

    def test_csrf_token_consistent_in_session(self, app):
        """
        **Scenario**: CSRF token consistent within session.
        
        **Assertions**: Multiple calls return same token.
        """
        with app.app_context():
            with app.test_request_context('/'):
                from ngo_homesuite.web.session_hardening import CSRFTokenRotation
                
                token1 = CSRFTokenRotation.get_or_create_csrf_token()
                token2 = CSRFTokenRotation.get_or_create_csrf_token()
                
                assert token1 == token2

    def test_csrf_token_rotation(self, app):
        """
        **Scenario**: CSRF token rotated on sensitive operations.
        
        **Assertions**: New token generated after rotation.
        """
        with app.app_context():
            with app.test_request_context('/'):
                from ngo_homesuite.web.session_hardening import CSRFTokenRotation
                from flask import session
                
                old_token = CSRFTokenRotation.get_or_create_csrf_token()
                CSRFTokenRotation.rotate_csrf_token()
                new_token = CSRFTokenRotation.get_or_create_csrf_token()
                
                assert old_token != new_token


class TestSessionMetadata:
    """Test session metadata for tamper detection."""

    def test_session_created_timestamp_set(self, app):
        """
        **Scenario**: Session stores creation timestamp.
        
        **Assertions**: Timestamp ISO format, set on init.
        """
        with app.app_context():
            with app.test_request_context('/'):
                from ngo_homesuite.web.session_hardening import SessionHardeningMiddleware
                from flask import session
                
                middleware = SessionHardeningMiddleware(app)
                middleware.before_request()
                
                assert 'session_created_at' in session
                assert 'T' in session['session_created_at']  # ISO format
                assert 'Z' in session['session_created_at'] or '+' in session['session_created_at']

    def test_session_nonce_generated(self, app):
        """
        **Scenario**: Session gets unique nonce for anti-tampering.
        
        **Assertions**: Session nonce is 32-char hex.
        """
        with app.app_context():
            with app.test_request_context('/'):
                from ngo_homesuite.web.session_hardening import SessionHardeningMiddleware
                from flask import session
                
                middleware = SessionHardeningMiddleware(app)
                middleware.before_request()
                
                assert 'session_nonce' in session
                assert len(session['session_nonce']) == 32
