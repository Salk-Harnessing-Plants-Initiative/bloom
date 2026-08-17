"""The copy ledger — which object version currently sits on Box.

Kept apart from backup_lib.py because it is the one piece with state and a
lifecycle: a SQLite file under /var/lib that outlives any single run and is
what makes a multi-day seed resumable.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone
from typing import Sequence

from backup_lib import SQLITE_MAX_VARIABLES, StorageObject, chunked

SCHEMA_VERSION = 1


class Ledger:
    """SQLite record of which object *version* is currently on Box.

    Keyed by (bucket_id, name) — the Box path — with the copied version
    stored as a value. An overwrite in Supabase mints a new version, so a
    version mismatch is exactly the signal to re-copy. This is what makes
    the multi-day seed resumable and the weekly run cheap: neither one has
    to list the Box destination.

    Copy workers run in a thread pool and each records its own success, so
    every method takes the instance lock and the connection is opened with
    `check_same_thread=False`. Without both, the first concurrent copy dies
    on SQLite's same-thread check.
    """

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn
        self._lock = threading.Lock()
        self._migrate()

    @classmethod
    def open(cls, path: str) -> "Ledger":
        conn = sqlite3.connect(path, check_same_thread=False)
        conn.execute("PRAGMA journal_mode=WAL")
        return cls(conn)

    def _migrate(self) -> None:
        self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS copied (
                bucket_id  TEXT NOT NULL,
                name       TEXT NOT NULL,
                version    TEXT,
                size       INTEGER,
                copied_at  TEXT NOT NULL,
                PRIMARY KEY (bucket_id, name)
            );
            CREATE TABLE IF NOT EXISTS runs (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                started_at  TEXT NOT NULL,
                finished_at TEXT,
                outcome     TEXT,
                stats       TEXT
            );
            CREATE TABLE IF NOT EXISTS meta (
                key   TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            """
        )
        self.conn.execute(
            "INSERT OR IGNORE INTO meta (key, value) VALUES ('schema_version', ?)",
            (str(SCHEMA_VERSION),),
        )
        self.conn.commit()

    def copied_versions(self) -> dict[tuple[str, str], str | None]:
        with self._lock:
            rows = self.conn.execute(
                "SELECT bucket_id, name, version FROM copied"
            ).fetchall()
        return {(bucket, name): version for bucket, name, version in rows}

    def versions_for(
        self, keys: Sequence[tuple[str, str]]
    ) -> dict[tuple[str, str], str | None]:
        """Copied versions for just these objects.

        The seed walks millions of rows; loading the whole `copied` table to
        plan one batch would put the entire object list in memory twice. One
        indexed lookup per batch keeps the run flat instead.
        """
        found: dict[tuple[str, str], str | None] = {}
        for chunk in chunked(list(keys), SQLITE_MAX_VARIABLES // 2):
            placeholders = ", ".join(["(?, ?)"] * len(chunk))
            params = [value for key in chunk for value in key]
            with self._lock:
                rows = self.conn.execute(
                    "SELECT bucket_id, name, version FROM copied "
                    f"WHERE (bucket_id, name) IN ({placeholders})",
                    params,
                ).fetchall()
            found.update({(bucket, name): version for bucket, name, version in rows})
        return found

    def mark_copied(self, obj: StorageObject, now: str | None = None) -> None:
        with self._lock:
            self.conn.execute(
                "INSERT INTO copied (bucket_id, name, version, size, copied_at) "
                "VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT (bucket_id, name) DO UPDATE SET "
                "version=excluded.version, size=excluded.size, copied_at=excluded.copied_at",
                (obj.bucket_id, obj.name, obj.version, obj.size, now or utcnow()),
            )

    def commit(self) -> None:
        with self._lock:
            self.conn.commit()

    def start_run(self, now: str | None = None) -> int:
        with self._lock:
            cur = self.conn.execute(
                "INSERT INTO runs (started_at) VALUES (?)", (now or utcnow(),)
            )
            self.conn.commit()
            return int(cur.lastrowid)

    def finish_run(
        self, run_id: int, outcome: str, stats: dict, now: str | None = None
    ) -> None:
        with self._lock:
            self.conn.execute(
                "UPDATE runs SET finished_at=?, outcome=?, stats=? WHERE id=?",
                (now or utcnow(), outcome, json.dumps(stats, sort_keys=True), run_id),
            )
            self.conn.commit()

    def last_successful_run(self) -> str | None:
        """Start time of the last clean run — the `--since` watermark.

        Deliberately the *start* time, not the finish time: an object
        written while the run was in flight has an `updated_at` after the
        start, so anchoring on the start re-checks it next week instead of
        letting it fall through the gap.
        """
        with self._lock:
            row = self.conn.execute(
                "SELECT started_at FROM runs WHERE outcome='ok' "
                "ORDER BY id DESC LIMIT 1"
            ).fetchone()
        return row[0] if row else None

    def close(self) -> None:
        with self._lock:
            self.conn.close()


def utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+00")

