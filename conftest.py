"""Pytest setup for the HA-level tests.

The engine unit tests in tests/test_engine.py do not need Home
Assistant; the HA-level tests use pytest-homeassistant-custom-component
and need the repository root on sys.path so HA's loader can import the
``custom_components`` package.
"""

import pytest

pytest_plugins = "pytest_homeassistant_custom_component"


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Enable loading custom integrations from this repository."""
    yield
