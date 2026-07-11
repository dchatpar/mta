"""MTa Credit Management — tenants, balances, usage logging.

SQLite-backed, async-friendly via aiosqlite-style wrapper (sync calls in
executor pool). Stores tenant balances, daily/monthly limits, API key hashes,
and per-call usage logs for billing and quota enforcement.

Schema:
- tenants (id, name, api_key_hash, api_key_prefix, balance, daily_limit,
  monthly_limit, rate_limit_per_minute, suspended, created_at)
- usage_log (id, tenant_id, endpoint, recipients_count, cost, status,
  message_id, ts)
- pricing (id, plan_name, monthly_fee, included_messages, per_message_cost,
  max_recipients_per_message, rate_per_minute)
- api_keys (id, tenant_id, key_hash, key_prefix, name, created_at, last_used)

Public API:
- create_tenant(name, ...) -> Tenant
- list_tenants() -> [Tenant]
- get_tenant(tenant_id) -> Tenant|None
- update_tenant(tenant_id, **fields) -> Tenant
- delete_tenant(tenant_id) -> bool
- topup(tenant_id, amount) -> Tenant
- record_usage(tenant_id, endpoint, recipients_count, status, message_id=None) -> UsageLog
- get_usage(tenant_id, days=30) -> [UsageLog]
- get_daily_total(tenant_id, day) -> int
- get_monthly_total(tenant_id, year, month) -> int
- authenticate_api_key(key) -> Tenant|None
- ensure_schema()
"""
import sqlite3
import secrets
import hashlib
import time
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import List, Optional, Dict, Any
from dataclasses import dataclass, asdict, field

DB_PATH = Path("/opt/mta/data/credits.db")
_DB_LOCK = threading.Lock()


def _hash_key(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


def generate_api_key(prefix: str = "mta") -> str:
    """Generate a fresh API key. Format: mta_<32 random hex>."""
    return f"{prefix}_{secrets.token_hex(24)}"


@dataclass
class Tenant:
    id: int
    name: str
    api_key_hash: str
    api_key_prefix: str
    balance: int
    daily_limit: int
    monthly_limit: int
    rate_limit_per_minute: int
    suspended: bool
    created_at: float

    def to_dict(self, include_plain_key: str = None) -> Dict[str, Any]:
        d = asdict(self)
        if include_plain_key:
            d["api_key"] = include_plain_key
        return d


@dataclass
class UsageLog:
    id: int
    tenant_id: int
    endpoint: str
    recipients_count: int
    cost: int
    status: str
    message_id: Optional[str]
    ts: float

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@contextmanager
def _conn():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(str(DB_PATH), timeout=10)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("PRAGMA foreign_keys=ON")
    try:
        yield c
        c.commit()
    finally:
        c.close()


SCHEMA = """
CREATE TABLE IF NOT EXISTS tenants (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    api_key_hash TEXT NOT NULL,
    api_key_prefix TEXT NOT NULL,
    balance INTEGER NOT NULL DEFAULT 0,
    daily_limit INTEGER NOT NULL DEFAULT 0,
    monthly_limit INTEGER NOT NULL DEFAULT 0,
    rate_limit_per_minute INTEGER NOT NULL DEFAULT 60,
    suspended INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS usage_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant_id INTEGER NOT NULL,
    endpoint TEXT NOT NULL,
    recipients_count INTEGER NOT NULL DEFAULT 0,
    cost INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL,
    message_id TEXT,
    ts REAL NOT NULL,
    FOREIGN KEY(tenant_id) REFERENCES tenants(id)
);
CREATE INDEX IF NOT EXISTS idx_usage_tenant_ts ON usage_log(tenant_id, ts);
CREATE TABLE IF NOT EXISTS pricing (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    plan_name TEXT NOT NULL UNIQUE,
    monthly_fee INTEGER NOT NULL DEFAULT 0,
    included_messages INTEGER NOT NULL DEFAULT 0,
    per_message_cost INTEGER NOT NULL DEFAULT 1,
    max_recipients_per_message INTEGER NOT NULL DEFAULT 100,
    rate_per_minute INTEGER NOT NULL DEFAULT 60
);
CREATE TABLE IF NOT EXISTS api_keys (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant_id INTEGER NOT NULL,
    key_hash TEXT NOT NULL UNIQUE,
    key_prefix TEXT NOT NULL,
    name TEXT NOT NULL DEFAULT 'default',
    created_at REAL NOT NULL,
    last_used REAL,
    FOREIGN KEY(tenant_id) REFERENCES tenants(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_api_keys_tenant ON api_keys(tenant_id);
"""


def ensure_schema() -> None:
    with _DB_LOCK, _conn() as c:
        c.executescript(SCHEMA)
        # Seed default pricing if empty
        cur = c.execute("SELECT COUNT(*) AS n FROM pricing")
        if cur.fetchone()["n"] == 0:
            c.executemany(
                "INSERT INTO pricing(plan_name, monthly_fee, included_messages, "
                "per_message_cost, max_recipients_per_message, rate_per_minute) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                [
                    ("free", 0, 100, 1, 10, 10),
                    ("starter", 900, 5000, 1, 100, 120),
                    ("pro", 4500, 50000, 1, 1000, 600),
                    ("scale", 18000, 250000, 1, 5000, 3000),
                ],
            )


def create_tenant(name: str, balance: int = 1000,
                  daily_limit: int = 0, monthly_limit: int = 0,
                  rate_limit_per_minute: int = 60,
                  api_key_name: str = "default") -> tuple:
    """Create tenant + return (Tenant, plain_api_key).

    Plain API key is shown ONLY at creation.
    """
    ensure_schema()
    plain_key = generate_api_key()
    key_hash = _hash_key(plain_key)
    key_prefix = plain_key[:12]
    with _DB_LOCK, _conn() as c:
        cur = c.execute(
            "INSERT INTO tenants(name, api_key_hash, api_key_prefix, balance, "
            "daily_limit, monthly_limit, rate_limit_per_minute, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (name, key_hash, key_prefix, balance, daily_limit,
             monthly_limit, rate_limit_per_minute, time.time()),
        )
        tenant_id = cur.lastrowid
        c.execute(
            "INSERT INTO api_keys(tenant_id, key_hash, key_prefix, name, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (tenant_id, key_hash, key_prefix, api_key_name, time.time()),
        )
    t = get_tenant(tenant_id)
    return t, plain_key


def _row_to_tenant(row) -> Tenant:
    return Tenant(
        id=row["id"], name=row["name"], api_key_hash=row["api_key_hash"],
        api_key_prefix=row["api_key_prefix"], balance=row["balance"],
        daily_limit=row["daily_limit"], monthly_limit=row["monthly_limit"],
        rate_limit_per_minute=row["rate_limit_per_minute"],
        suspended=bool(row["suspended"]), created_at=row["created_at"],
    )


def list_tenants() -> List[Tenant]:
    ensure_schema()
    with _conn() as c:
        rows = c.execute("SELECT * FROM tenants ORDER BY id").fetchall()
    return [_row_to_tenant(r) for r in rows]


def get_tenant(tenant_id: int) -> Optional[Tenant]:
    ensure_schema()
    with _conn() as c:
        row = c.execute("SELECT * FROM tenants WHERE id=?", (tenant_id,)).fetchone()
    return _row_to_tenant(row) if row else None


def update_tenant(tenant_id: int, **fields) -> Optional[Tenant]:
    """Update mutable fields: balance, daily_limit, monthly_limit,
    rate_limit_per_minute, suspended, name."""
    allowed = {"balance", "daily_limit", "monthly_limit",
               "rate_limit_per_minute", "suspended", "name"}
    sets, vals = [], []
    for k, v in fields.items():
        if k not in allowed:
            continue
        if k == "suspended":
            v = 1 if v else 0
        sets.append(f"{k}=?")
        vals.append(v)
    if not sets:
        return get_tenant(tenant_id)
    vals.append(tenant_id)
    with _DB_LOCK, _conn() as c:
        c.execute(f"UPDATE tenants SET {', '.join(sets)} WHERE id=?", vals)
    return get_tenant(tenant_id)


def delete_tenant(tenant_id: int) -> bool:
    with _DB_LOCK, _conn() as c:
        cur = c.execute("DELETE FROM tenants WHERE id=?", (tenant_id,))
    return cur.rowcount > 0


def topup(tenant_id: int, amount: int) -> Optional[Tenant]:
    if amount <= 0:
        raise ValueError("amount must be positive")
    with _DB_LOCK, _conn() as c:
        c.execute("UPDATE tenants SET balance = balance + ? WHERE id=?", (amount, tenant_id))
    return get_tenant(tenant_id)


def authenticate_api_key(key: str) -> Optional[Tenant]:
    if not key:
        return None
    key_hash = _hash_key(key)
    with _conn() as c:
        row = c.execute(
            "SELECT t.* FROM tenants t JOIN api_keys k ON k.tenant_id = t.id "
            "WHERE k.key_hash=? AND t.suspended=0",
            (key_hash,),
        ).fetchone()
        if row:
            c.execute("UPDATE api_keys SET last_used=? WHERE key_hash=?",
                      (time.time(), key_hash))
    return _row_to_tenant(row) if row else None


def record_usage(tenant_id: int, endpoint: str,
                 recipients_count: int = 0, cost: int = 0,
                 status: str = "ok", message_id: Optional[str] = None) -> UsageLog:
    ensure_schema()
    with _DB_LOCK, _conn() as c:
        cur = c.execute(
            "INSERT INTO usage_log(tenant_id, endpoint, recipients_count, "
            "cost, status, message_id, ts) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (tenant_id, endpoint, recipients_count, cost, status,
             message_id, time.time()),
        )
        usage_id = cur.lastrowid
    return UsageLog(id=usage_id, tenant_id=tenant_id, endpoint=endpoint,
                    recipients_count=recipients_count, cost=cost,
                    status=status, message_id=message_id, ts=time.time())


def get_usage(tenant_id: int, days: int = 30) -> List[UsageLog]:
    cutoff = time.time() - days * 86400
    with _conn() as c:
        rows = c.execute(
            "SELECT * FROM usage_log WHERE tenant_id=? AND ts>=? "
            "ORDER BY ts DESC",
            (tenant_id, cutoff),
        ).fetchall()
    return [UsageLog(id=r["id"], tenant_id=r["tenant_id"],
                     endpoint=r["endpoint"],
                     recipients_count=r["recipients_count"], cost=r["cost"],
                     status=r["status"], message_id=r["message_id"],
                     ts=r["ts"]) for r in rows]


def get_daily_total(tenant_id: int) -> int:
    """Sum of recipients_count for today (UTC)."""
    import datetime
    midnight = datetime.datetime.utcnow().replace(
        hour=0, minute=0, second=0, microsecond=0).timestamp()
    with _conn() as c:
        row = c.execute(
            "SELECT COALESCE(SUM(recipients_count),0) AS s FROM usage_log "
            "WHERE tenant_id=? AND ts>=? AND status='ok'",
            (tenant_id, midnight),
        ).fetchone()
    return int(row["s"] or 0)


def get_monthly_total(tenant_id: int) -> int:
    """Sum of recipients_count this calendar month (UTC)."""
    import datetime
    now = datetime.datetime.utcnow()
    first = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0).timestamp()
    with _conn() as c:
        row = c.execute(
            "SELECT COALESCE(SUM(recipients_count),0) AS s FROM usage_log "
            "WHERE tenant_id=? AND ts>=? AND status='ok'",
            (tenant_id, first),
        ).fetchone()
    return int(row["s"] or 0)


def check_quota(tenant: Tenant, recipients: int = 1) -> Dict[str, Any]:
    """Pre-flight check: balance, daily, monthly. Returns dict with
    {ok: bool, reason: str, current_balance, daily_used, monthly_used}."""
    if tenant.suspended:
        return {"ok": False, "reason": "suspended", "http": 403}
    if tenant.balance < recipients:
        return {"ok": False, "reason": "insufficient_credits",
                "balance": tenant.balance, "needed": recipients,
                "http": 402}
    daily_used = get_daily_total(tenant.id)
    if tenant.daily_limit and (daily_used + recipients) > tenant.daily_limit:
        return {"ok": False, "reason": "daily_limit_exceeded",
                "daily_used": daily_used, "daily_limit": tenant.daily_limit,
                "http": 429}
    monthly_used = get_monthly_total(tenant.id)
    if tenant.monthly_limit and (monthly_used + recipients) > tenant.monthly_limit:
        return {"ok": False, "reason": "monthly_limit_exceeded",
                "monthly_used": monthly_used, "monthly_limit": tenant.monthly_limit,
                "http": 429}
    return {"ok": True, "balance": tenant.balance,
            "daily_used": daily_used, "monthly_used": monthly_used}


def decrement_balance(tenant_id: int, amount: int) -> int:
    """Atomic decrement. Returns new balance."""
    if amount < 0:
        raise ValueError("amount must be >= 0")
    with _DB_LOCK, _conn() as c:
        c.execute("UPDATE tenants SET balance = balance - ? WHERE id=? AND balance >= ?",
                  (amount, tenant_id, amount))
        row = c.execute("SELECT balance FROM tenants WHERE id=?", (tenant_id,)).fetchone()
    return int(row["balance"]) if row else 0


def stats_summary() -> Dict[str, Any]:
    ensure_schema()
    with _conn() as c:
        tenants = c.execute("SELECT COUNT(*) AS n, "
                            "SUM(CASE WHEN suspended=0 THEN 1 ELSE 0 END) AS active, "
                            "SUM(balance) AS total_balance FROM tenants").fetchone()
        usage = c.execute(
            "SELECT COUNT(*) AS calls, COALESCE(SUM(recipients_count),0) AS recipients, "
            "COALESCE(SUM(cost),0) AS cost "
            "FROM usage_log WHERE ts>=?",
            (time.time() - 86400,),
        ).fetchone()
    return {
        "tenants_total": tenants["n"] or 0,
        "tenants_active": tenants["active"] or 0,
        "total_balance": tenants["total_balance"] or 0,
        "last_24h_calls": usage["calls"] or 0,
        "last_24h_recipients": usage["recipients"] or 0,
        "last_24h_cost": usage["cost"] or 0,
    }