"""Shared pytest fixtures for the estimator test suite."""

import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture
def client() -> TestClient:
    """FastAPI test client wired to the full application (routers + dependencies)."""
    return TestClient(app)
