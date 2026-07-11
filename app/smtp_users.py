"""SMTP user storage + auth helper used by the mail engine auth handler.

BUG-A6 — provides the SMTPAuth class that main.py imports for:
  - /api/v1/smtp-users (CRUD)
  - /api/internal/smtp-auth (kumod callback)

Uses a separate SQLite DB at /opt/mta/data/smtp_users.db (or MTA_DATA_DIR).
Passwords stored as salt$pbkdf2_sha256$(digest_hex) — no plaintext.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

DB_PATH = Path(os.environ.get("MTA_DATA_DIR", "/opt/mta/data")) / "smtp_users.db"


def _connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(str(DB_PATH), timeout=10)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    return c


SCHEMA = """
CREATE TABLE IF NOT EXISTS smtp_users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    username TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    tenant_id TEXT,
    active INTEGER NOT NULL DEFAULT 1,
    created_at REAL NOT NULL,
    rotated_at REAL
);
CREATE INDEX IF NOT EXISTS idx_smtp_users_username ON smtp_users(username);
"""


def ensure_schema() -> None:
    with _connect() as c:
        c.executescript(SCHEMA)


def _hash_password(password: str, salt: Optional[str] = None) -> str:
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt.encode("utf-8"), 50_000
    ).hex()
    return f"{salt}$pbkdf2_sha256${digest}"


def _verify_password(password: str, stored: str) -> bool:
    try:
        salt, algo, expected = stored.split("$", 2)
    except ValueError:
        return False
    if algo != "pbkdf2_sha256":
        return False
    actual = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt.encode("utf-8"), 50_000
    ).hex()
    return hmac.compare_digest(expected, actual)


@dataclass
class SMTPUser:
    id: int
    name: str
    username: str
    tenant_id: Optional[str]
    active: bool
    created_at: float
    rotated_at: Optional[float]

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "username": self.username,
            "tenant_id": self.tenant_id,
            "active": bool(self.active),
            "created_at": self.created_at,
            "rotated_at": self.rotated_at,
        }


def _row_to_user(row: sqlite3.Row) -> SMTPUser:
    return SMTPUser(
        id=row["id"],
        name=row["name"],
        username=row["username"],
        tenant_id=row["tenant_id"],
        active=bool(row["active"]),
        created_at=row["created_at"],
        rotated_at=row["rotated_at"],
    )


class SMTPAuth:
    """Static-style facade so main.py can use it as a namespace."""

    @staticmethod
    def create(name: str, username: str, password: str, tenant_id: Optional[str] = None) -> SMTPUser:
        ensure_schema()
        pw_hash = _hash_password(password)
        try:
            with _connect() as c:
                cur = c.execute(
                    "INSERT INTO smtp_users(name, username, password_hash, tenant_id, active, created_at) "
                    "VALUES (?, ?, ?, ?, 1, ?)",
                    (name, username, pw_hash, tenant_id, time.time()),
                )
                uid = cur.lastrowid
                row = c.execute("SELECT * FROM smtp_users WHERE id=?", (uid,)).fetchone()
        except sqlite3.IntegrityError as e:
            raise ValueError(f"smtp user '{username}' already exists") from e
        return _row_to_user(row)

    @staticmethod
    def list_all(include_inactive: bool = False) -> List[SMTPUser]:
        ensure_schema()
        with _connect() as c:
            if include_inactive:
                rows = c.execute("SELECT * FROM smtp_users ORDER BY id").fetchall()
            else:
                rows = c.execute("SELECT * FROM smtp_users WHERE active=1 ORDER BY id").fetchall()
        return [_row_to_user(r) for r in rows]

    @staticmethod
    def get(uid: int) -> Optional[SMTPUser]:
        ensure_schema()
        with _connect() as c:
            row = c.execute("SELECT * FROM smtp_users WHERE id=?", (uid,)).fetchone()
        return _row_to_user(row) if row else None

    @staticmethod
    def revoke(uid: int) -> bool:
        ensure_schema()
        with _connect() as c:
            cur = c.execute(
                "UPDATE smtp_users SET active=0, rotated_at=? WHERE id=? AND active=1",
                (time.time(), uid),
            )
        return cur.rowcount > 0

    @staticmethod
    def rotate(uid: int, new_password: str) -> Optional[SMTPUser]:
        ensure_schema()
        pw_hash = _hash_password(new_password)
        with _connect() as c:
            cur = c.execute(
                "UPDATE smtp_users SET password_hash=?, rotated_at=? WHERE id=? AND active=1",
                (pw_hash, time.time(), uid),
            )
            if cur.rowcount == 0:
                return None
            row = c.execute("SELECT * FROM smtp_users WHERE id=?", (uid,)).fetchone()
        return _row_to_user(row)

    @staticmethod
    def verify(username: str, password: str) -> bool:
        ensure_schema()
        with _connect() as c:
            row = c.execute(
                "SELECT * FROM smtp_users WHERE username=? AND active=1",
                (username,),
            ).fetchone()
        if not row:
            return False
        return _verify_password(password, row["password_hash"])
