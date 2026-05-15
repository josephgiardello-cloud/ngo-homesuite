"""
Pytest configuration for NGO HomeSuite tests.

This module handles test environment setup, including:
- Clearing SQLCipher key to test with unencrypted DB
- Temporary DB setup/teardown
- Fixture management
"""

import os
import pytest

from ngo_homesuite.app_factory import create_app
from ngo_homesuite.flask_config import TestingConfig


def pytest_configure(config):
    """
    Hook that runs before test collection.
    Clear NGO_HOMESUITE_DB_KEY to allow tests to run with unencrypted DB.
    """
    # Store original key if set
    original_key = os.environ.get("NGO_HOMESUITE_DB_KEY")
    
    # Clear it for test runs (tests use temp unencrypted DBs)
    if "NGO_HOMESUITE_DB_KEY" in os.environ:
        del os.environ["NGO_HOMESUITE_DB_KEY"]
    
    # Store in config for potential restoration
    config.original_db_key = original_key


@pytest.fixture(scope="session")
def shared_test_app():
    return create_app(TestingConfig)


@pytest.fixture(scope="session", autouse=True)
def cleanup_test_env():
    """
    Session-scoped fixture to ensure clean test environment.
    Runs automatically before all tests.
    """
    # Ensure no DB key is set for tests
    os.environ.pop("NGO_HOMESUITE_DB_KEY", None)
    
    yield
    
    # Cleanup after all tests complete (optional)
    os.environ.pop("NGO_HOMESUITE_DB_KEY", None)


@pytest.fixture(autouse=True)
def isolate_db_env():
    """
    Function-scoped fixture to isolate DB env per test.
    Prevents test pollution from DB state.
    """
    # Clear DB key before each test
    original = os.environ.pop("NGO_HOMESUITE_DB_KEY", None)
    
    yield
    
    # Restore if it was set (for consecutive test runs)
    if original:
        os.environ["NGO_HOMESUITE_DB_KEY"] = original
    else:
        os.environ.pop("NGO_HOMESUITE_DB_KEY", None)
