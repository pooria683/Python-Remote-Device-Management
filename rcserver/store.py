"""SQLite storage for targets, command history and audit events.

No agent, no pairing codes: the controller connects directly to the target
using the target's built-in remote-access service (WinRM / OpenSSH / ADB).
"""

from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import Any, Iterator

from . import config, crypto

LOCK = threading.RLock()

SCHEMA = """
CREATE TABLE IF NOT EXISTS targets (
    id            TEXT PRIMARY KEY,
    name          TEXT NOT NULL,
    kind          TEXT NOT NULL,
    host          TEXT NOT NULL,
    port          INTEGER,
    username      TEXT,
    password_enc  TEXT,
    key_path      TEXT,
    domain        TEXT,
    platform      TEXT,
    use_https     INTEGER NOT NULL DEFAULT 0,
    insecure_tls  INTEGER NOT NULL DEFAULT 0,
    os_name       TEXT,
    status        TEXT NOT NULL DEFAULT 'unknown',
    last_test_at  TEXT,
    last_error    TEXT,
    created_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS commands (
    id          TEXT PRIMARY KEY,
    target_id   TEXT NOT NULL,
    kind        TEXT NOT NULL,
    input       TEXT,
    output      TEXT,
    exit_code   INTEGER,
    error       TEXT,
    duration_ms INTEGER,
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS events (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    target_id  TEXT,
    level      TEXT NOT NULL DEFAULT 'info',
    message    TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_commands_target ON commands (target_id, created_at);
CREATE INDEX IF NOT EXISTS idx_events_created ON events (created_at);
"""


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def iso(value: datetime) -> str:
    return value.isoformat(timespec="seconds")


def parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


@contextmanager
def db() -> Iterator[sqlite3.Connection]:
    conn = sqlite3.connect(config.DB_PATH, timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db() -> None:
    with LOCK, db() as conn:
        conn.executescript(SCHEMA)


def query(sql: str, args: tuple = (), one: bool = False) -> Any:
    with LOCK, db() as conn:
        rows = conn.execute(sql, args).fetchall()
    if one:
        return rows[0] if rows else None
    return rows


def execute(sql: str, args: tuple = ()) -> int:
    with LOCK, db() as conn:
        return conn.execute(sql, args).lastrowid


# --------------------------------------------------------------------------- #
# audit log
# --------------------------------------------------------------------------- #
def log_event(target_id: str | None, level: str, message: str) -> None:
    try:
        with LOCK, db() as conn:
            conn.execute(
                "INSERT INTO events (target_id, level, message, created_at) VALUES (?, ?, ?, ?)",
                (target_id, level, message, iso(utcnow())),
            )
            conn.execute(
                "DELETE FROM events WHERE created_at < ?",
                (iso(utcnow() - timedelta(days=14)),),
            )
    except sqlite3.Error:
        pass


def list_events(limit: int = 100) -> list[dict]:
    rows = query("SELECT * FROM events ORDER BY id DESC LIMIT ?", (max(1, min(limit, 500)),))
    return [dict(row) for row in rows]


# --------------------------------------------------------------------------- #
# targets
# --------------------------------------------------------------------------- #
def default_port(kind: str, use_https: bool = False) -> int | None:
    if kind == "winrm":
        return config.DEFAULT_PORTS["winrm_https" if use_https else "winrm"]
    if kind == "ssh":
        return config.DEFAULT_PORTS["ssh"]
    return None


def target_public(row: sqlite3.Row) -> dict:
    data = dict(row)
    password_enc = data.pop("password_enc", None) or ""
    data["has_password"] = bool(password_enc)
    data["has_key"] = bool(data.get("key_path"))
    data["port"] = data.get("port") or default_port(data["kind"], bool(data.get("use_https")))
    return data


def list_targets() -> list[dict]:
    rows = query("SELECT * FROM targets ORDER BY created_at DESC")
    return [target_public(row) for row in rows]


def get_target(target_id: str) -> sqlite3.Row | None:
    return query("SELECT * FROM targets WHERE id = ?", (target_id,), one=True)


def create_target(data: dict) -> dict:
    kind = str(data.get("kind") or "")
    if kind not in config.KINDS:
        raise ValueError("نوع اتصال پشتیبانی نمی‌شود")
    host = str(data.get("host") or "").strip()
    if not host:
        raise ValueError("آدرس دستگاه لازم است")

    target_id = uuid.uuid4().hex
    now = iso(utcnow())
    with LOCK, db() as conn:
        conn.execute(
            """INSERT INTO targets
               (id, name, kind, host, port, username, password_enc, key_path, domain,
                platform, use_https, insecure_tls, status, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'unknown', ?)""",
            (
                target_id,
                str(data.get("name") or host)[:120],
                kind,
                host[:200],
                int(data["port"]) if data.get("port") else default_port(kind, bool(data.get("use_https"))),
                str(data.get("username") or "")[:200] or None,
                crypto.encrypt(str(data.get("password") or "")),
                str(data.get("key_path") or "")[:500] or None,
                str(data.get("domain") or "")[:120] or None,
                str(data.get("platform") or ("android" if kind == "adb" else "windows" if kind == "winrm" else "auto"))[:40],
                1 if data.get("use_https") else 0,
                1 if data.get("insecure_tls") else 0,
                now,
            ),
        )
    log_event(target_id, "info", f"target added: {data.get('name') or host} ({kind})")
    row = get_target(target_id)
    return target_public(row)


def delete_target(target_id: str) -> bool:
    row = get_target(target_id)
    if not row:
        return False
    with LOCK, db() as conn:
        conn.execute("DELETE FROM targets WHERE id = ?", (target_id,))
        conn.execute("DELETE FROM commands WHERE target_id = ?", (target_id,))
    log_event(None, "warning", f"target removed: {row['name']}")
    return True


def set_test_result(target_id: str, status: str, os_name: str | None = None, error: str | None = None) -> None:
    with LOCK, db() as conn:
        conn.execute(
            """UPDATE targets SET status = ?, os_name = COALESCE(?, os_name),
               last_test_at = ?, last_error = ? WHERE id = ?""",
            (status, os_name, iso(utcnow()), error, target_id),
        )


def get_secret(target: sqlite3.Row) -> str:
    return crypto.decrypt(target["password_enc"] or "")


# --------------------------------------------------------------------------- #
# command history
# --------------------------------------------------------------------------- #
def log_command(
    target_id: str,
    kind: str,
    input_text: str,
    output: str | None,
    exit_code: int | None,
    error: str | None,
    duration_ms: int,
) -> str:
    command_id = uuid.uuid4().hex
    with LOCK, db() as conn:
        conn.execute(
            """INSERT INTO commands
               (id, target_id, kind, input, output, exit_code, error, duration_ms, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                command_id,
                target_id,
                kind,
                (input_text or "")[:4000],
                (output or "")[: config.MAX_RESULT_CHARS],
                exit_code,
                (error or "")[:4000],
                duration_ms,
                iso(utcnow()),
            ),
        )
    return command_id


def list_commands(target_id: str, limit: int = 30) -> list[dict]:
    rows = query(
        "SELECT * FROM commands WHERE target_id = ? ORDER BY created_at DESC LIMIT ?",
        (target_id, max(1, min(limit, 200))),
    )
    commands = []
    for row in reversed(rows):
        data = dict(row)
        if data.get("output") and len(data["output"]) > config.MAX_LIST_RESULT_CHARS:
            data["output"] = data["output"][: config.MAX_LIST_RESULT_CHARS] + "\n… (truncated)"
        commands.append(data)
    return commands


def command_stats(target_id: str) -> dict:
    row = query(
        """SELECT
             SUM(CASE WHEN error IS NULL OR error = '' THEN 1 ELSE 0 END) AS ok,
             SUM(CASE WHEN error IS NOT NULL AND error <> '' THEN 1 ELSE 0 END) AS failed,
             COUNT(*) AS total
           FROM commands WHERE target_id = ?""",
        (target_id,),
        one=True,
    )
    return {
        "ok": (row["ok"] or 0) if row else 0,
        "failed": (row["failed"] or 0) if row else 0,
        "total": (row["total"] or 0) if row else 0,
    }
