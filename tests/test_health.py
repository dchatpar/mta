"""Health endpoint tests — 5 tests."""
from fastapi.testclient import TestClient


def test_health_returns_ok(client):
    r = client.get("/api/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["service"] == "mta"
    assert "version" in body


def test_health_no_auth_required(client):
    """Health must be accessible without auth so K8s/Fly healthchecks work."""
    r = client.get("/api/health")
    assert r.status_code == 200
    assert "WWW-Authenticate" not in r.headers


def test_engine_health_alive(client):
    r = client.get("/api/engine/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["engine"] is True


def test_engine_machine_info(client):
    r = client.get("/api/engine/machine-info")
    assert r.status_code == 200
    info = r.json()
    # Engine should return some known keys
    assert isinstance(info, dict)
    assert "result" in info or "cpu_count" in info or len(info) > 0


def test_engine_metrics_json(client):
    r = client.get("/api/engine/metrics.json")
    assert r.status_code == 200
    data = r.json()
    # Prometheus-style metrics JSON
    assert isinstance(data, (dict, list))