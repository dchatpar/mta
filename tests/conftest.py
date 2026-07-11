"""Shared pytest fixtures for the MTa test suite.

Strategy:
- Use FastAPI TestClient against the live MTa app (which talks to the live kumod
  engine + live DNS).
- Use a tempdir for SQLite DBs (credits.db, webhooks.db) so tests don't pollute
  production data.
- Auth helpers return a Bearer session token (created via the login endpoint).
- For M3 tests, monkeypatch the M3 client to return canned responses (don't
  burn API tokens in CI).
"""
import os
import sys
import shutil
import tempfile
import sqlite3
import secrets
from pathlib import Path
import pytest
from fastapi.testclient import TestClient

# Force the app to use temp DBs before importing
_TMP = Path(tempfile.mkdtemp(prefix="mta-test-"))
os.environ["MTA_DATA_DIR"] = str(_TMP)
os.environ["MTA_TEST_MODE"] = "1"

# Patch DB paths via env at import time (we monkeypatch at module level below)
sys.path.insert(0, "/opt/mta/app")

# Patch the DB paths in credits / webhooks / cloudflare BEFORE importing main
import credits
import webhooks
import cloudflare

credits.DB_PATH = _TMP / "credits.db"
webhooks.DB_PATH = _TMP / "webhooks.db"
cloudflare.TOKEN_PATH = _TMP / "cf-token.enc"
cloudflare.FERNET_KEY_PATH = _TMP / ".fernet-key"

# Now import app
from main import app  # noqa: E402


@pytest.fixture(scope="session")
def tmp_data_dir():
    return _TMP


@pytest.fixture(autouse=True)
def _clear_client_cookies(client):
    """Reset cookies between tests to prevent session leak.

    Several auth tests assert that *unauthenticated* endpoints reject. With a
    session-scoped TestClient, cookies set by earlier tests (login_success,
    me_with_cookie_only) would leak and silently authorize later requests.
    Clearing the cookie jar makes each test start from a clean slate without
    re-creating the TestClient. SESSIONS dict is left alone — the session-scoped
    auth_token fixture keeps working via the Authorization header.
    """
    try:
        client.cookies.clear()
    except Exception:
        pass
    yield


@pytest.fixture(scope="session")
def client():
    """FastAPI TestClient — session-scoped for speed."""
    with TestClient(app) as c:
        yield c


@pytest.fixture(scope="session")
def auth_token(client):
    """Log in with PIN 0000 (default) and return the bearer token."""
    r = client.post("/api/auth/login", json={"pin": "0000"})
    assert r.status_code == 200, f"login failed: {r.text}"
    return r.json()["token"]


@pytest.fixture(scope="session")
def auth_headers(auth_token):
    return {"Authorization": f"Bearer {auth_token}"}


@pytest.fixture
def created_tenant(client, auth_headers):
    """Create a fresh tenant for tests that need one. Returns dict with api_key."""
    name = f"test-{secrets.token_hex(4)}"
    r = client.post("/api/v1/credits/tenants", json={"name": name, "balance": 10000}, headers=auth_headers)
    assert r.status_code == 200, f"create tenant failed: {r.text}"
    data = r.json()
    data["plaintext_api_key"] = data.get("api_key")  # shown once on create
    return data


@pytest.fixture(autouse=True)
def reset_credit_tables():
    """Clean credits tables before each test that needs isolation."""
    yield
    # best-effort cleanup (we share session DB)
    try:
        with sqlite3.connect(str(credits.DB_PATH)) as c:
            c.execute("DELETE FROM usage_log")
            c.execute("DELETE FROM tenants WHERE name LIKE 'test-%'")
    except Exception:
        pass


def pytest_configure(config):
    """Hook: print summary at end."""
    pass