"""Local durable send ledger with file lock and unique daily key."""

from __future__ import annotations

import sqlite3
import time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Iterator

BLOCKING_STATES = frozenset({"pending", "sent", "uncertain", "replayed"})


@dataclass(frozen=True, slots=True)
class LedgerRow:
    organization_id: str
    employee_id: str
    period: str
    local_date: date
    status: str
    provider_message_id: str | None
    idempotency_key: str


class Ledger:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.lock_path = path.with_suffix(path.suffix + ".lock")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=30000")
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS send_ledger (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    organization_id TEXT NOT NULL,
                    employee_id TEXT NOT NULL,
                    period TEXT NOT NULL,
                    local_date TEXT NOT NULL,
                    status TEXT NOT NULL,
                    provider_message_id TEXT,
                    idempotency_key TEXT NOT NULL UNIQUE,
                    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
                    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
                    UNIQUE (organization_id, employee_id, period, local_date)
                )
                """
            )

    @staticmethod
    def idempotency_key(
        organization_id: str,
        employee_id: str,
        period: str,
        local_date: date,
    ) -> str:
        return f"{organization_id}|{employee_id}|{period}|{local_date.isoformat()}"

    def get(
        self,
        *,
        organization_id: str,
        employee_id: str,
        period: str,
        local_date: date,
    ) -> LedgerRow | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT organization_id, employee_id, period, local_date, status,
                       provider_message_id, idempotency_key
                FROM send_ledger
                WHERE organization_id = ?
                  AND employee_id = ?
                  AND period = ?
                  AND local_date = ?
                """,
                (
                    organization_id,
                    employee_id,
                    period,
                    local_date.isoformat(),
                ),
            ).fetchone()
        if row is None:
            return None
        return LedgerRow(
            organization_id=row["organization_id"],
            employee_id=row["employee_id"],
            period=row["period"],
            local_date=date.fromisoformat(row["local_date"]),
            status=row["status"],
            provider_message_id=row["provider_message_id"],
            idempotency_key=row["idempotency_key"],
        )

    def blocks_send(
        self,
        *,
        organization_id: str,
        employee_id: str,
        period: str,
        local_date: date,
    ) -> bool:
        existing = self.get(
            organization_id=organization_id,
            employee_id=employee_id,
            period=period,
            local_date=local_date,
        )
        return existing is not None and existing.status in BLOCKING_STATES

    def try_insert_pending(
        self,
        *,
        organization_id: str,
        employee_id: str,
        period: str,
        local_date: date,
    ) -> bool:
        """Insert a pending claim. Returns False if a blocking row already exists."""
        key = self.idempotency_key(organization_id, employee_id, period, local_date)
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                existing = conn.execute(
                    """
                    SELECT status FROM send_ledger
                    WHERE organization_id = ? AND employee_id = ?
                      AND period = ? AND local_date = ?
                    """,
                    (
                        organization_id,
                        employee_id,
                        period,
                        local_date.isoformat(),
                    ),
                ).fetchone()
                if existing is not None and existing["status"] in BLOCKING_STATES:
                    conn.execute("COMMIT")
                    return False
                if existing is not None:
                    conn.execute(
                        """
                        UPDATE send_ledger
                        SET status = 'pending',
                            provider_message_id = NULL,
                            updated_at = strftime('%Y-%m-%dT%H:%M:%fZ','now')
                        WHERE organization_id = ? AND employee_id = ?
                          AND period = ? AND local_date = ?
                        """,
                        (
                            organization_id,
                            employee_id,
                            period,
                            local_date.isoformat(),
                        ),
                    )
                else:
                    conn.execute(
                        """
                        INSERT INTO send_ledger (
                            organization_id, employee_id, period, local_date,
                            status, provider_message_id, idempotency_key
                        ) VALUES (?, ?, ?, ?, 'pending', NULL, ?)
                        """,
                        (
                            organization_id,
                            employee_id,
                            period,
                            local_date.isoformat(),
                            key,
                        ),
                    )
                conn.execute("COMMIT")
                return True
            except Exception:
                conn.execute("ROLLBACK")
                raise

    def finalize(
        self,
        *,
        organization_id: str,
        employee_id: str,
        period: str,
        local_date: date,
        status: str,
        provider_message_id: str | None = None,
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE send_ledger
                SET status = ?,
                    provider_message_id = ?,
                    updated_at = strftime('%Y-%m-%dT%H:%M:%fZ','now')
                WHERE organization_id = ? AND employee_id = ?
                  AND period = ? AND local_date = ?
                """,
                (
                    status,
                    provider_message_id,
                    organization_id,
                    employee_id,
                    period,
                    local_date.isoformat(),
                ),
            )

    @contextmanager
    def exclusive_lock(self, *, timeout_seconds: float = 30.0) -> Iterator[None]:
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        deadline = time.monotonic() + timeout_seconds
        handle = open(self.lock_path, "a+b")
        try:
            if handle.tell() == 0:
                handle.write(b"0")
                handle.flush()
            while True:
                try:
                    _lock_file(handle)
                    break
                except OSError:
                    if time.monotonic() >= deadline:
                        handle.close()
                        raise TimeoutError(
                            f"Could not acquire ledger lock within {timeout_seconds}s"
                        )
                    time.sleep(0.05)
            yield
        finally:
            try:
                _unlock_file(handle)
            finally:
                handle.close()


def _lock_file(handle: object) -> None:
    try:
        import msvcrt

        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        return
    except ImportError:
        pass
    import fcntl

    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)


def _unlock_file(handle: object) -> None:
    try:
        import msvcrt

        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        return
    except ImportError:
        pass
    except OSError:
        return
    try:
        import fcntl

        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    except Exception:
        return
