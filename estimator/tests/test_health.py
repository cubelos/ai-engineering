"""Smoke test for the liveness endpoint."""

from fastapi.testclient import TestClient


def test_health_returns_200(client: TestClient) -> None:
    """GET /health responds 200 with status healthy."""
    response = client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "healthy"
