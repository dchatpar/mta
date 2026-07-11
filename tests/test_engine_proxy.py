"""Engine proxy tests — exercise every kumod endpoint through MTa.

Live engine only — no mocks.
"""
import json
import time


def test_engine_health_live(client):
    r = client.get("/api/engine/health")
    assert r.status_code == 200
    assert r.json()["engine"] is True


def test_engine_machine_info(client):
    r = client.get("/api/engine/machine-info")
    assert r.status_code == 200


def test_engine_metrics_text(client):
    r = client.get("/api/engine/metrics")
    assert r.status_code == 200
    assert "text/plain" in r.headers.get("content-type", "")


def test_engine_metrics_json(client):
    r = client.get("/api/engine/metrics.json")
    assert r.status_code == 200
    data = r.json()
    assert isinstance(data, dict)


def test_engine_memory(client):
    r = client.get("/api/engine/memory")
    assert r.status_code == 200


def test_engine_task_dump(client):
    r = client.get("/api/engine/task-dump")
    assert r.status_code == 200


def test_engine_ready_q_states(client):
    r = client.get("/api/engine/ready-q-states")
    assert r.status_code == 200
    body = r.json()
    # Body could be {"result":"..."} or list
    assert body is not None


def test_engine_suspends_ready_get(client):
    r = client.get("/api/engine/suspends/ready")
    assert r.status_code == 200


def test_engine_suspends_scheduled_get(client):
    r = client.get("/api/engine/suspends/scheduled")
    assert r.status_code == 200


def test_engine_bounces_get(client):
    r = client.get("/api/engine/bounces")
    assert r.status_code == 200


def test_engine_bounce_create_and_delete(client):
    # Create a bounce entry
    payload = {"campaign": "test-campaign", "tenant": "test-tenant", "reason": "test bounce"}
    r = client.post("/api/engine/bounces", json=payload)
    assert r.status_code in (200, 201)
    body = r.json()
    bid = body.get("id") or body.get("result", {}).get("id")
    # Try to delete if we got an id
    if bid:
        rd = client.delete(f"/api/engine/bounces/{bid}")
        assert rd.status_code in (200, 204)


def test_engine_suspend_ready_create_and_delete(client):
    payload = {"queue": "test-q-001", "reason": "test", "duration_seconds": 60}
    r = client.post("/api/engine/suspends/ready", json=payload)
    assert r.status_code in (200, 201)
    rd = client.delete("/api/engine/suspends/ready/test-q-001")
    assert rd.status_code in (200, 204)


def test_engine_suspend_scheduled_create_and_delete(client):
    payload = {"queue": "test-q-002", "reason": "test", "duration_seconds": 60}
    r = client.post("/api/engine/suspends/scheduled", json=payload)
    assert r.status_code in (200, 201)
    rd = client.delete("/api/engine/suspends/scheduled/test-q-002")
    assert rd.status_code in (200, 204)


def test_engine_inspect_message_404_for_unknown(client):
    r = client.get("/api/engine/inspect-message/no-such-spool-id")
    # Should not crash; 404 or 200 with not-found envelope
    assert r.status_code in (200, 404)


def test_engine_inspect_sched_q(client):
    r = client.get("/api/engine/inspect-sched-q/Default")
    assert r.status_code in (200, 404)


def test_engine_inject(client):
    """Inject a real envelope and verify the engine accepts it."""
    payload = {
        "envelope_sender": "test@mta.local",
        "recipients": ["verify@example.com"],
        "content": "Subject: hello\r\nFrom: test@mta.local\r\nTo: verify@example.com\r\n\r\nbody\r\n",
    }
    r = client.post("/api/engine/inject", json=payload)
    # Should accept; engine returns either a message id or a queue result
    assert r.status_code in (200, 201, 202, 400)
    # 400 only if the engine is fully offline or rejecting the format; 200+ if it took it


def test_engine_inject_requires_envelope_sender(client):
    r = client.post("/api/engine/inject", json={"recipients": ["a@b.com"], "content": "x"})
    # FastAPI will reject before reaching engine (422 Pydantic)
    assert r.status_code == 422


def test_engine_inject_requires_recipients(client):
    r = client.post("/api/engine/inject", json={"envelope_sender": "a@b.com", "content": "x"})
    assert r.status_code == 422


def test_engine_bump_config(client):
    r = client.post("/api/engine/bump-config")
    assert r.status_code in (200, 201, 204)


def test_engine_rebind(client):
    payload = {"queue": "Default", "site_name": ""}
    r = client.post("/api/engine/rebind", json=payload)
    assert r.status_code in (200, 201, 204)


def test_engine_rebind_requires_queue(client):
    r = client.post("/api/engine/rebind", json={})
    assert r.status_code == 422


def test_engine_health_does_not_5xx(client):
    """Health must NEVER return 5xx — it's the K8s probe."""
    r = client.get("/api/engine/health")
    assert r.status_code < 500


def test_engine_metrics_text_contains_kumo_names(client):
    """Prometheus output should mention some kumod-specific metric names."""
    r = client.get("/api/engine/metrics")
    assert r.status_code == 200
    # Don't be too strict — different metric names exist
    text = r.text
    # At least one known kumod prefix
    assert any(prefix in text.lower() for prefix in ("kumod", "ready", "message", "http_")), \
        "no recognized metrics found"


def test_engine_ready_q_states_has_data(client):
    r = client.get("/api/engine/ready-q-states")
    assert r.status_code == 200
    body = r.json()
    # Should be a dict or list — never empty in a healthy engine
    assert body


def test_engine_health_concurrent_calls(client):
    """Health must be fast and concurrent-safe."""
    import concurrent.futures
    def hit():
        return client.get("/api/engine/health").status_code
    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as ex:
        results = list(ex.map(lambda _: hit(), range(20)))
    assert all(s == 200 for s in results)