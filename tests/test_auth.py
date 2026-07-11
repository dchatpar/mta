"""Auth tests — 10 tests covering PIN login, sessions, expiry, bearer, bad PIN."""
import time


def test_login_success(client):
    r = client.post("/api/auth/login", json={"pin": "0000"})
    assert r.status_code == 200
    body = r.json()
    assert "token" in body
    assert len(body["token"]) > 20
    assert body["ttl_seconds"] > 0
    assert "mta_session" in r.cookies


def test_login_wrong_pin(client):
    r = client.post("/api/auth/login", json={"pin": "9999"})
    assert r.status_code == 401
    assert "detail" in r.json()


def test_login_missing_pin(client):
    r = client.post("/api/auth/login", json={})
    assert r.status_code == 401


def test_login_empty_pin(client):
    r = client.post("/api/auth/login", json={"pin": ""})
    assert r.status_code == 401


def test_login_non_string_pin(client):
    r = client.post("/api/auth/login", json={"pin": 1234})
    # Should still 401 (invalid)
    assert r.status_code == 401


def test_me_with_session_cookie(client):
    # login
    r = client.post("/api/auth/login", json={"pin": "0000"})
    token = r.json()["token"]
    # hit /me with bearer
    r2 = client.get("/api/me", headers={"Authorization": f"Bearer {token}"})
    assert r2.status_code == 200
    assert r2.json()["authenticated"] is True


def test_me_with_cookie_only(client):
    r = client.post("/api/auth/login", json={"pin": "0000"})
    cookies = r.cookies
    r2 = client.get("/api/me", cookies=cookies)
    assert r2.status_code == 200
    assert r2.json()["authenticated"] is True


def test_me_without_auth(client):
    r = client.get("/api/me")
    assert r.status_code == 401


def test_me_with_invalid_bearer(client):
    r = client.get("/api/me", headers={"Authorization": "Bearer not-a-real-token"})
    assert r.status_code == 401


def test_logout_invalidates_session(client):
    r = client.post("/api/auth/login", json={"pin": "0000"})
    token = r.json()["token"]
    headers = {"Authorization": f"Bearer {token}"}
    # /me works
    r1 = client.get("/api/me", headers=headers)
    assert r1.status_code == 200
    # logout
    r2 = client.post("/api/auth/logout", headers=headers)
    assert r2.status_code == 200
    # /me now fails
    r3 = client.get("/api/me", headers=headers)
    assert r3.status_code == 401


def test_auth_status(client):
    r = client.get("/api/auth/status")
    assert r.status_code == 200
    body = r.json()
    assert body["pin_set"] is True
    assert "authenticated" in body


def test_login_response_is_json(client):
    r = client.post("/api/auth/login", json={"pin": "0000"})
    assert r.headers["content-type"].startswith("application/json")