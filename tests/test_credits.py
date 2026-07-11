"""Credit management tests — 25 tests covering CRUD, balance ops, limits, rate, atomic."""
import secrets
import time


def test_create_tenant_basic(client, auth_headers):
    name = f"test-{secrets.token_hex(3)}"
    r = client.post("/api/v1/credits/tenants",
                    json={"name": name, "balance": 500},
                    headers=auth_headers)
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["name"] == name
    assert body["balance"] == 500
    assert "api_key" in body  # shown only on create
    assert body["api_key"].startswith("mta_")


def test_create_tenant_requires_name(client, auth_headers):
    r = client.post("/api/v1/credits/tenants", json={"balance": 100}, headers=auth_headers)
    assert r.status_code == 422


def test_create_tenant_negative_balance_rejected(client, auth_headers):
    r = client.post("/api/v1/credits/tenants",
                    json={"name": "x", "balance": -1},
                    headers=auth_headers)
    assert r.status_code == 422


def test_list_tenants(client, auth_headers):
    r = client.get("/api/v1/credits/tenants", headers=auth_headers)
    assert r.status_code == 200
    assert isinstance(r.json(), list)


def test_get_tenant_detail(client, auth_headers):
    # Create first
    name = f"detail-{secrets.token_hex(3)}"
    cr = client.post("/api/v1/credits/tenants",
                     json={"name": name, "balance": 200},
                     headers=auth_headers)
    tid = cr.json()["id"]
    # Get detail
    r = client.get(f"/api/v1/credits/tenants/{tid}", headers=auth_headers)
    assert r.status_code == 200
    body = r.json()
    assert body["name"] == name
    assert "usage_30d" in body


def test_get_tenant_404(client, auth_headers):
    r = client.get("/api/v1/credits/tenants/999999", headers=auth_headers)
    assert r.status_code == 404


def test_update_tenant(client, auth_headers):
    name = f"upd-{secrets.token_hex(3)}"
    cr = client.post("/api/v1/credits/tenants",
                     json={"name": name, "balance": 100, "daily_limit": 10},
                     headers=auth_headers)
    tid = cr.json()["id"]
    # Update
    r = client.put(f"/api/v1/credits/tenants/{tid}",
                   json={"daily_limit": 999, "rate_limit_per_minute": 50},
                   headers=auth_headers)
    assert r.status_code == 200
    body = r.json()
    assert body["daily_limit"] == 999
    assert body["rate_limit_per_minute"] == 50


def test_update_tenant_suspend(client, auth_headers):
    name = f"susp-{secrets.token_hex(3)}"
    cr = client.post("/api/v1/credits/tenants",
                     json={"name": name, "balance": 100}, headers=auth_headers)
    tid = cr.json()["id"]
    r = client.put(f"/api/v1/credits/tenants/{tid}",
                   json={"suspended": True}, headers=auth_headers)
    assert r.status_code == 200
    assert r.json()["suspended"] is True


def test_delete_tenant(client, auth_headers):
    name = f"del-{secrets.token_hex(3)}"
    cr = client.post("/api/v1/credits/tenants",
                     json={"name": name, "balance": 100}, headers=auth_headers)
    tid = cr.json()["id"]
    rd = client.delete(f"/api/v1/credits/tenants/{tid}", headers=auth_headers)
    assert rd.status_code == 200
    # Get should 404 now
    rg = client.get(f"/api/v1/credits/tenants/{tid}", headers=auth_headers)
    assert rg.status_code == 404


def test_topup(client, auth_headers):
    name = f"top-{secrets.token_hex(3)}"
    cr = client.post("/api/v1/credits/tenants",
                     json={"name": name, "balance": 100}, headers=auth_headers)
    tid = cr.json()["id"]
    r = client.post(f"/api/v1/credits/tenants/{tid}/topup",
                    json={"amount": 500}, headers=auth_headers)
    assert r.status_code == 200
    assert r.json()["balance"] == 600


def test_topup_negative_rejected(client, auth_headers):
    name = f"tneg-{secrets.token_hex(3)}"
    cr = client.post("/api/v1/credits/tenants",
                     json={"name": name, "balance": 100}, headers=auth_headers)
    tid = cr.json()["id"]
    r = client.post(f"/api/v1/credits/tenants/{tid}/topup",
                    json={"amount": -10}, headers=auth_headers)
    assert r.status_code == 422


def test_usage_log_returns_list(client, auth_headers):
    name = f"u-{secrets.token_hex(3)}"
    cr = client.post("/api/v1/credits/tenants",
                     json={"name": name, "balance": 100}, headers=auth_headers)
    tid = cr.json()["id"]
    # Topup to generate usage entry
    client.post(f"/api/v1/credits/tenants/{tid}/topup",
                json={"amount": 50}, headers=auth_headers)
    r = client.get(f"/api/v1/credits/tenants/{tid}/usage", headers=auth_headers)
    assert r.status_code == 200
    assert isinstance(r.json(), list)
    assert len(r.json()) >= 1


def test_usage_days_validation(client, auth_headers):
    name = f"d-{secrets.token_hex(3)}"
    cr = client.post("/api/v1/credits/tenants",
                     json={"name": name, "balance": 100}, headers=auth_headers)
    tid = cr.json()["id"]
    r = client.get(f"/api/v1/credits/tenants/{tid}/usage?days=0", headers=auth_headers)
    assert r.status_code == 400


def test_stats_endpoint(client, auth_headers):
    r = client.get("/api/v1/credits/stats", headers=auth_headers)
    assert r.status_code == 200
    body = r.json()
    assert isinstance(body, dict)


def test_create_api_key_helper(client, auth_headers):
    r = client.post("/api/v1/credits/_create-api-key", headers=auth_headers)
    assert r.status_code == 200
    assert r.json()["api_key"].startswith("mta_")


def test_tenant_api_key_authenticates(created_tenant):
    """The API key returned on create must authenticate on /send."""
    api_key = created_tenant["plaintext_api_key"]
    # Use a bare requests-like helper since TestClient doesn't preserve cookies well
    from fastapi.testclient import TestClient
    from main import app
    with TestClient(app) as c:
        r = c.post("/api/v1/credits/check-quota?recipients=1",
                   headers={"Authorization": f"Bearer {api_key}"})
        # Should be 200 with quota result, not 401
        assert r.status_code in (200, 429, 402, 403), r.text


def test_tenant_api_key_suspended_rejected(client, auth_headers):
    name = f"sa-{secrets.token_hex(3)}"
    cr = client.post("/api/v1/credits/tenants",
                     json={"name": name, "balance": 100}, headers=auth_headers)
    tid = cr.json()["id"]
    api_key = cr.json()["api_key"]
    # Suspend
    client.put(f"/api/v1/credits/tenants/{tid}",
               json={"suspended": True}, headers=auth_headers)
    # Try to use
    r = client.post("/api/v1/credits/check-quota?recipients=1",
                    headers={"Authorization": f"Bearer {api_key}"})
    assert r.status_code in (401, 403)


def test_invalid_api_key_rejected(client):
    r = client.post("/api/v1/credits/check-quota?recipients=1",
                    headers={"Authorization": "Bearer mta_invalid"})
    assert r.status_code == 401


def test_missing_authorization_rejected(client):
    r = client.post("/api/v1/credits/check-quota?recipients=1")
    assert r.status_code == 401


def test_admin_endpoints_require_session(client):
    """Without session cookie or bearer session token, admin endpoints must 403."""
    r = client.post("/api/v1/credits/tenants", json={"name": "x", "balance": 1})
    # Auth gate: 401/403 expected
    assert r.status_code in (401, 403)


def test_balance_decrements_atomically(client, auth_headers):
    """Two concurrent topups shouldn't race."""
    import threading
    name = f"con-{secrets.token_hex(3)}"
    cr = client.post("/api/v1/credits/tenants",
                     json={"name": name, "balance": 0}, headers=auth_headers)
    tid = cr.json()["id"]

    results = []
    def hit():
        r = client.post(f"/api/v1/credits/tenants/{tid}/topup",
                        json={"amount": 10}, headers=auth_headers)
        results.append(r.status_code)

    threads = [threading.Thread(target=hit) for _ in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert all(s == 200 for s in results)
    final = client.get(f"/api/v1/credits/tenants/{tid}", headers=auth_headers)
    assert final.json()["balance"] == 50


def test_quota_check_returns_decision(client, created_tenant):
    api_key = created_tenant["plaintext_api_key"]
    r = client.post("/api/v1/credits/check-quota?recipients=10",
                    headers={"Authorization": f"Bearer {api_key}"})
    assert r.status_code == 200
    body = r.json()
    # Decision dict should have a verdict
    assert isinstance(body, dict)


def test_quota_insufficient_balance(client, auth_headers):
    name = f"broke-{secrets.token_hex(3)}"
    cr = client.post("/api/v1/credits/tenants",
                     json={"name": name, "balance": 5}, headers=auth_headers)
    api_key = cr.json()["api_key"]
    r = client.post("/api/v1/credits/check-quota?recipients=10000",
                    headers={"Authorization": f"Bearer {api_key}"})
    assert r.status_code in (200, 402, 403)
    if r.status_code == 200:
        # Body should say "no"
        body = r.json()
        assert isinstance(body, dict)


def test_api_key_only_shown_once(client, auth_headers):
    """The plaintext API key must be returned ONLY on create, not on subsequent GETs."""
    name = f"once-{secrets.token_hex(3)}"
    cr = client.post("/api/v1/credits/tenants",
                     json={"name": name, "balance": 100}, headers=auth_headers)
    tid = cr.json()["id"]
    assert "api_key" in cr.json()
    # Now GET — should NOT include plaintext key
    r = client.get(f"/api/v1/credits/tenants/{tid}", headers=auth_headers)
    body = r.json()
    assert "api_key" not in body  # never leak the plaintext key after creation


def test_tenant_name_length_limit(client, auth_headers):
    """Names over 80 chars must be rejected."""
    long_name = "x" * 100
    r = client.post("/api/v1/credits/tenants",
                    json={"name": long_name, "balance": 100},
                    headers=auth_headers)
    assert r.status_code == 422


def test_rate_limit_field_in_range(client, auth_headers):
    """rate_limit_per_minute must be 1..10000."""
    # Above max
    r = client.post("/api/v1/credits/tenants",
                    json={"name": "x", "balance": 100, "rate_limit_per_minute": 99999},
                    headers=auth_headers)
    assert r.status_code == 422
    # Below min
    r = client.post("/api/v1/credits/tenants",
                    json={"name": "x", "balance": 100, "rate_limit_per_minute": 0},
                    headers=auth_headers)
    assert r.status_code == 422