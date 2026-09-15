"""Reading storage.objects and the database's clock with psql, over the network.

The job reaches the database by its service name on the stack's network, the
way every other service does. The session rules are the job's own: read-only,
the SQL on stdin, and rows streamed to disk rather than held in memory.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

import backup_lib as lib

# Rows psql pulls per cursor fetch, so the manifest read streams instead of
# buffering millions of rows.
FETCH_COUNT = 10_000

# Sent ahead of the SQL in every session. The job connects as the Postgres
# superuser, so nothing server-side would refuse a write; this is the only thing
# that does. One constant, so a new query cannot be added without it.
READ_ONLY_PREAMBLE = "SET default_transaction_read_only = on;\n"

# A host that does not answer fails in seconds instead of holding the run lock.
CONNECT_TIMEOUT_SECONDS = 10


class PostgresError(lib.BackupError):
    """psql could not start, could not connect, or its query failed."""


@dataclass(frozen=True)
class Connection:
    host: str
    port: int
    user: str
    database: str
    # Kept out of the repr: the run's whole log reaches the job summary.
    password: str = field(repr=False)


def _psql(conn: Connection, *, streaming: bool) -> list[str]:
    """The psql argv. Everything secret travels elsewhere: SQL on stdin, the password in env."""
    psql = shutil.which("psql")
    if not psql:
        raise PostgresError("psql is not on PATH")
    argv = [
        psql,
        "-h",
        conn.host,
        "-p",
        str(conn.port),
        "-U",
        conn.user,
        "-d",
        conn.database,
        # A missing password fails as "no password supplied", not as a wrong one.
        "--no-password",
        "--no-align",
        "--tuples-only",
    ]
    if streaming:
        argv += ["--field-separator", "\t"]
    argv += ["--quiet", "--no-psqlrc", "-v", "ON_ERROR_STOP=1"]
    if streaming:
        # Without it psql buffers the whole result before writing a byte.
        argv += ["-v", f"FETCH_COUNT={FETCH_COUNT}"]
    return argv + ["-f", "-"]


def _env(conn: Connection) -> dict[str, str]:
    return {
        **os.environ,
        "PGPASSWORD": conn.password,
        "PGCONNECT_TIMEOUT": str(CONNECT_TIMEOUT_SECONDS),
    }


def _failed(returncode: int, stderr: str) -> PostgresError:
    return PostgresError(
        f"psql failed ({returncode}): {stderr.strip() or '(no stderr)'}"
    )


def query_to_file(conn: Connection, sql: str, destination: Path) -> int:
    """Run a read-only query, streaming its rows to a file. Returns the row count.

    `storage.objects` has millions of rows, so they go straight to disk. The
    SQL arrives on stdin so a long bucket list can never hit the argv length
    limit, and the session is pinned read-only so a mistake in query
    construction cannot write.
    """
    preamble = READ_ONLY_PREAMBLE + "SET statement_timeout = '60min';\n"
    with destination.open("w", encoding="utf-8") as out:
        try:
            process = subprocess.Popen(
                _psql(conn, streaming=True),
                stdin=subprocess.PIPE,
                stdout=out,
                stderr=subprocess.PIPE,
                text=True,
                env=_env(conn),
            )
        except OSError as exc:
            raise PostgresError(f"could not start psql: {exc}") from exc
        _, stderr = process.communicate(input=preamble + sql + ";\n")
    if process.returncode != 0:
        raise _failed(process.returncode, stderr)
    with destination.open(encoding="utf-8") as handle:
        return sum(1 for line in handle if line.strip())


def database_now(conn: Connection) -> str:
    """The database's clock, in the format the manifest reports updated_at in.

    The watermark is compared against `storage.objects.updated_at`, which
    Postgres writes. Taking it from this host instead compares two clocks: if
    this one ever runs ahead, objects written inside the skew are never
    enumerated again, silently and permanently.

    The SQL goes on stdin so the read-only preamble precedes it:
    `default_transaction_read_only` only governs transactions opened after it
    is set.
    """
    sql = "SELECT to_char(now() AT TIME ZONE 'UTC', 'YYYY-MM-DD\"T\"HH24:MI:SSOF');\n"
    try:
        result = subprocess.run(
            _psql(conn, streaming=False),
            # --quiet suppresses the `SET` tag, so the first line is the timestamp.
            input=READ_ONLY_PREAMBLE + sql,
            capture_output=True,
            text=True,
            env=_env(conn),
        )
    except OSError as exc:
        raise PostgresError(f"could not start psql: {exc}") from exc
    if result.returncode != 0:
        raise _failed(result.returncode, result.stderr)
    out = result.stdout.strip()
    if not out:
        raise PostgresError("could not read the database clock for the watermark")
    return out.splitlines()[0].strip()
