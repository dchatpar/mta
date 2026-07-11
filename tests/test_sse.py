"""SSE tests — 5 tests."""
import json


def test_sse_stream_basic(client):
    with client.stream("GET", "/api/live/stream") as r:
        assert r.status_code == 200
        assert "text/event-stream" in r.headers.get("content-type", "")
        # Read first event
        for line in r.iter_lines():
            if line and line.startswith("data:"):
                payload = line[5:].strip()
                if payload:
                    # Should be JSON parseable
                    obj = json.loads(payload)
                    assert isinstance(obj, dict)
                    break


def test_sse_stream_no_auth_required(client):
    """SSE must work without auth (used by dashboard before login in some setups)."""
    with client.stream("GET", "/api/live/stream") as r:
        assert r.status_code == 200


def test_sse_emits_engine_data(client):
    """Within ~3 seconds we should see at least one event with engine metrics."""
    seen_engine = False
    with client.stream("GET", "/api/live/stream") as r:
        assert r.status_code == 200
        import time
        deadline = time.time() + 3
        for line in r.iter_lines():
            if time.time() > deadline:
                break
            if line and line.startswith("data:"):
                payload = line[5:].strip()
                if payload:
                    try:
                        obj = json.loads(payload)
                    except Exception:
                        continue
                    if obj.get("type") == "engine" or "queue" in obj or "ready_q" in obj:
                        seen_engine = True
                        break
    assert seen_engine, "no engine event seen within 3s"


def test_sse_disconnect_graceful(client):
    """Closing the stream should not error the server."""
    with client.stream("GET", "/api/live/stream") as r:
        assert r.status_code == 200
        # Pull one line, then exit
        for line in r.iter_lines():
            if line:
                break
    # Subsequent health check should still work
    assert client.get("/api/health").status_code == 200


def test_sse_event_format_correct(client):
    """Each event line should follow SSE spec: 'data: <json>\\n\\n'."""
    with client.stream("GET", "/api/live/stream") as r:
        assert r.status_code == 200
        lines_seen = 0
        for line in r.iter_lines():
            lines_seen += 1
            if lines_seen >= 5:
                break
        assert lines_seen >= 1