"""Webhook delivery: register URLs, sign payloads with HMAC-SHA256,
deliver with exponential backoff (1s, 5s, 30s, 5min, 1hr, 6hr).

Storage: SQLite at /opt/mta/data/webhooks.db.
"""
import asyncio
import hashlib
import hmac
import json
import secrets
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from threading import Lock
from typing import Optional, List, Dict, Any
import httpx

DB_PATH = Path("/opt/mta/data/webhooks.db")
_LOCK = Lock()

RETRY_DELAYS = [1, 5, 30, 300, 3600, 21600]  # seconds
MAX_ATTEMPTS = 6


@contextmanager
def _conn():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(str(DB_PATH), timeout=10)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    try:
        yield c
        c.commit()
    finally:
        c.close()


SCHEMA = """
CREATE TABLE IF NOT EXISTS webhooks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    url TEXT NOT NULL,
    secret TEXT NOT NULL,
    events TEXT NOT NULL,
    active INTEGER NOT NULL DEFAULT 1,
    description TEXT,
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS deliveries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    webhook_id INTEGER NOT NULL,
    event TEXT NOT NULL,
    payload TEXT NOT NULL,
    status TEXT NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0,
    last_attempt REAL,
    next_attempt REAL,
    last_response_code INTEGER,
    last_error TEXT,
    delivered_at REAL,
    created_at REAL NOT NULL,
    FOREIGN KEY(webhook_id) REFERENCES webhooks(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_deliveries_next ON deliveries(next_attempt);
CREATE INDEX IF NOT EXISTS idx_deliveries_status ON deliveries(status);
"""


def ensure_schema():
    with _LOCK, _conn() as c:
        c.executescript(SCHEMA)


def sign_payload(secret: str, payload: str) -> str:
    return hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()


def register_webhook(url: str, events: List[str], description: str = "") -> Dict[str, Any]:
    ensure_schema()
    secret = "whsec_" + secrets.token_hex(24)
    with _LOCK, _conn() as c:
        cur = c.execute(
            "INSERT INTO webhooks(url, secret, events, active, description, created_at) "
            "VALUES (?, ?, ?, 1, ?, ?)",
            (url, secret, ",".join(events), description, time.time()),
        )
        wid = cur.lastrowid
    return {"id": wid, "url": url, "secret": secret, "events": events,
            "active": True, "description": description}


def list_webhooks() -> List[Dict[str, Any]]:
    ensure_schema()
    with _conn() as c:
        rows = c.execute("SELECT * FROM webhooks ORDER BY id").fetchall()
    return [{"id": r["id"], "url": r["url"], "events": r["events"].split(","),
             "active": bool(r["active"]), "description": r["description"],
             "created_at": r["created_at"]} for r in rows]


def delete_webhook(webhook_id: int) -> bool:
    with _LOCK, _conn() as c:
        cur = c.execute("DELETE FROM webhooks WHERE id=?", (webhook_id,))
    return cur.rowcount > 0


def get_webhook(webhook_id: int) -> Optional[Dict[str, Any]]:
    ensure_schema()
    with _conn() as c:
        r = c.execute("SELECT * FROM webhooks WHERE id=?", (webhook_id,)).fetchone()
    if not r:
        return None
    return {"id": r["id"], "url": r["url"], "secret": r["secret"],
            "events": r["events"].split(","), "active": bool(r["active"]),
            "description": r["description"]}


async def fire_event(event: str, payload: Dict[str, Any]) -> List[int]:
    """Enqueue a webhook delivery for each active subscriber to this event.
    Returns list of delivery IDs. The async background worker delivers them."""
    ensure_schema()
    body = json.dumps({"event": event, "ts": time.time(),
                       "data": payload}, default=str)
    body_bytes = body.encode()
    delivery_ids = []
    with _conn() as c:
        rows = c.execute(
            "SELECT * FROM webhooks WHERE active=1 AND "
            "(events=? OR events='*' OR events LIKE ?)",
            (event, f"%,{event},%"),
        ).fetchall()
        # Better: do events LIKE '%eventname%'
        rows = c.execute(
            "SELECT * FROM webhooks WHERE active=1 AND events LIKE ?",
            (f"%{event}%",),
        ).fetchall()
        for r in rows:
            # Defensive: convert to str so hmac.new() never sees an int.
            secret = str(r["secret"])
            signature = sign_payload(secret, body)
            cur = c.execute(
                "INSERT INTO deliveries(webhook_id, event, payload, status, "
                "next_attempt, created_at) VALUES (?, ?, ?, 'pending', ?, ?)",
                (r["id"], event, body, time.time(), time.time()),
            )
            delivery_ids.append(cur.lastrowid)
    # Trigger background delivery (don't await — fire-and-forget)
    asyncio.create_task(_deliver_pending())
    return delivery_ids


async def _deliver_one(delivery_id: int) -> bool:
    """Attempt a single delivery. Returns True on 2xx."""
    with _conn() as c:
        r = c.execute(
            "SELECT d.*, w.url AS url, w.secret AS secret FROM deliveries d "
            "JOIN webhooks w ON w.id=d.webhook_id WHERE d.id=?",
            (delivery_id,),
        ).fetchone()
    if not r:
        return False
    if r["status"] == "delivered":
        return True
    if r["status"] == "failed":
        return False
    # BUG FIX: SQLite can return ints for secret/payload via dynamic typing.
    # Force-convert to str before .encode() so signing/POST never crashes.
    secret = str(r["secret"])
    payload = str(r["payload"])
    event = str(r["event"])
    url = str(r["url"])
    delivery_id_val = int(r["id"])
    signature = sign_payload(secret, payload)
    headers = {
        "Content-Type": "application/json",
        "X-MTa-Event": event,
        "X-MTa-Delivery": str(delivery_id_val),
        "X-MTa-Signature": f"sha256={signature}",
        "X-MTa-Timestamp": str(int(time.time())),
        "User-Agent": "MTa-Webhooks/1.0",
    }
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(url, content=payload.encode("utf-8"),
                                     headers=headers)
        ok = 200 <= resp.status_code < 300
    except Exception as e:
        ok = False
        err = str(e)
        code = 0
    else:
        code = resp.status_code
        err = None if ok else (resp.text or "")[:200]
    new_attempts = r["attempts"] + 1
    with _LOCK, _conn() as c:
        if ok:
            c.execute(
                "UPDATE deliveries SET status='delivered', attempts=?, "
                "last_attempt=?, last_response_code=?, delivered_at=? "
                "WHERE id=?",
                (new_attempts, time.time(), code, time.time(), delivery_id),
            )
            return True
        if new_attempts >= MAX_ATTEMPTS:
            c.execute(
                "UPDATE deliveries SET status='failed', attempts=?, "
                "last_attempt=?, last_response_code=?, last_error=? WHERE id=?",
                (new_attempts, time.time(), code, err, delivery_id),
            )
            return False
        delay = RETRY_DELAYS[min(new_attempts - 1, len(RETRY_DELAYS) - 1)]
        c.execute(
            "UPDATE deliveries SET attempts=?, last_attempt=?, "
            "last_response_code=?, last_error=?, next_attempt=? WHERE id=?",
            (new_attempts, time.time(), code, err, time.time() + delay, delivery_id),
        )
    return False


async def _deliver_pending():
    """Drain due pending deliveries. Called after each fire_event()."""
    now = time.time()
    with _conn() as c:
        rows = c.execute(
            "SELECT id FROM deliveries WHERE status='pending' "
            "AND next_attempt<=? ORDER BY id LIMIT 50",
            (now,),
        ).fetchall()
    ids = [r["id"] for r in rows]
    for did in ids:
        await _deliver_one(did)


async def retry_due():
    """Public helper called by a background scheduler."""
    await _deliver_pending()


def delivery_log(webhook_id: Optional[int] = None, limit: int = 100) -> List[Dict[str, Any]]:
    ensure_schema()
    with _conn() as c:
        if webhook_id:
            rows = c.execute(
                "SELECT * FROM deliveries WHERE webhook_id=? "
                "ORDER BY id DESC LIMIT ?",
                (webhook_id, limit),
            ).fetchall()
        else:
            rows = c.execute(
                "SELECT * FROM deliveries ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
    return [{"id": r["id"], "webhook_id": r["webhook_id"],
             "event": r["event"], "status": r["status"],
             "attempts": r["attempts"], "last_response_code": r["last_response_code"],
             "last_error": r["last_error"], "delivered_at": r["delivered_at"],
             "created_at": r["created_at"]} for r in rows]