"""The write rules for loading a dataset: each write is one request, sent once.

A write refused as unauthorised never ran, so it is the only one sent again, after signing
in afresh. A write whose outcome is unknown (a timeout, a 5xx) may still be finishing on the
server, so the next run waits that out before it reads anything.
"""

from __future__ import annotations

import json
import math
import os
import re
import time
from pathlib import Path
from typing import Any, Callable

from ._text import visible

# How long a write can keep running on the server after the client gave up: the statement
# timeout applying to the writer's requests, plus a margin.
STATEMENT_TIMEOUT_S = 300
WAIT_MARGIN_S = 30

RERUN = "run the same command again to continue"

# Rows per read. Each page is one request that has to finish inside the gateway's 60 s.
PAGE_ROWS = 5000

# PostgREST codes for a missing, invalid or expired token.
UNAUTHORISED_CODES = ("PGRST301", "PGRST303", 401, "401")

# What a dataset name may hold, since its counts objects are stored under it.
SAFE_DATASET_NAME = re.compile(r"[A-Za-z0-9._ -]+")
DOTS_ONLY = re.compile(r"\.+")

DATASET_COLUMNS = "id,name,deleted_at,source_checksum,ingested_at,metadata,n_cells"


class LoadError(RuntimeError):
    """Something about the file, the options or the dataset makes this unsafe to write."""


def _api_errors() -> tuple:
    """The exceptions a request can raise; anything else is a fault here and propagates."""
    import httpx
    from postgrest.exceptions import APIError

    return (httpx.HTTPError, APIError)


def classify(exc: BaseException) -> str:
    """'unauthorised' (never ran), 'failed' (did not commit) or 'unknown' (may have)."""
    import httpx
    from postgrest.exceptions import APIError

    if isinstance(exc, (httpx.ConnectError, httpx.ConnectTimeout)):
        return "failed"
    if isinstance(exc, httpx.HTTPError):
        return "unknown"
    if isinstance(exc, APIError):
        if exc.code in UNAUTHORISED_CODES:
            return "unauthorised"
        return "unknown" if isinstance(exc.code, int) and exc.code >= 500 else "failed"
    return "unknown"


class Marker:
    """When a write last ended with an unknown outcome, so the next run can wait it out."""

    def __init__(
        self,
        path: Path,
        wait_s: float = STATEMENT_TIMEOUT_S + WAIT_MARGIN_S,
        clock: Callable[[], float] = time.time,
    ):
        self.path, self.wait_s, self.clock = Path(path), wait_s, clock

    def record(self, step: str) -> None:
        """Written whole or not at all, readable only by this user."""
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"at": self.clock(), "step": step}))
        os.chmod(tmp, 0o600)
        os.replace(tmp, self.path)

    def check(self) -> None:
        """Refuse until a write that may still be running has certainly finished.

        A record that cannot be read is dated by the file itself, so it is waited out too.
        """
        if not self.path.exists():
            return
        try:
            record = json.loads(self.path.read_text())
            at, step = float(record["at"]), str(record["step"])
        except (ValueError, KeyError, TypeError):
            at, step = self.path.stat().st_mtime, "an earlier write"
        left = at + self.wait_s - self.clock()
        if left > 0:
            raise LoadError(
                f"'{visible(step)}' may still be finishing on the server; wait "
                f"{math.ceil(left)} seconds, then run the same command again"
            )
        self.path.unlink()

    def wait_text(self) -> str:
        return f"about {math.ceil(self.wait_s / 60)} minutes"


def marker_path(directory: Path, dataset_name: str) -> Path:
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", dataset_name.strip())
    return Path(directory) / f"unknown-write.{safe}.json"


class Writer:
    """Sends each write once; re-sends only after an unauthorised refusal."""

    def __init__(self, client: Any, renew: Callable[[], Any], marker: Marker):
        self.client, self._renew, self.marker = client, renew, marker

    def read(self, fetch: Callable[[Any], Any]) -> Any:
        """Run fetch(client); a read may be repeated, after one fresh sign-in."""
        for attempt in (1, 2):
            try:
                return fetch(self.client)
            except _api_errors() as exc:
                if classify(exc) == "unauthorised" and attempt == 1:
                    self.client = self._renew()
                    continue
                raise LoadError(f"reading failed: {exc}; {RERUN}") from None
        return None

    def write(self, step: str, send: Callable[[Any], Any]) -> Any:
        """Run send(client), one request, under the write rules.

        Interrupted mid-request (Ctrl-C), the write may still commit on the server, so that
        is recorded as an unknown outcome before the interrupt goes on.
        """
        for attempt in (1, 2):
            try:
                return send(self.client)
            except KeyboardInterrupt:
                self.marker.record(step)
                raise
            except _api_errors() as exc:
                kind = classify(exc)
                if kind == "unauthorised" and attempt == 1:
                    self.client = self._renew()
                    continue
                if kind == "unknown":
                    self.marker.record(step)
                    raise LoadError(
                        f"{step}: {exc}. Its outcome is unknown, so it may still be finishing "
                        f"on the server; wait {self.marker.wait_text()}, then {RERUN}"
                    ) from None
                raise LoadError(f"{step}: {exc}; {RERUN}") from None
        return None


def read_all(
    writer: Writer, table: str, columns: str, *, filters=(), order: str = "id",
    page: int = PAGE_ROWS,
) -> list[dict]:
    """Every matching row, one page per request, ordered by ``order``.

    ``filters`` are (method, column, value), e.g. ("eq", "dataset_id", 7).
    """
    rows: list[dict] = []
    start = 0
    while True:
        def fetch(client, start=start):
            query = client.table(table).select(columns)
            for method, column, value in filters:
                query = getattr(query, method)(column, value)
            return query.order(order).range(start, start + page - 1).execute().data

        batch = writer.read(fetch)
        rows.extend(batch)
        if len(batch) < page:
            return rows
        start += page


def insert(
    writer: Writer, step: str, table: str, rows: list[dict], *, returning: bool = False
) -> list[dict]:
    """Insert rows in one request, sent once."""
    def send(client):
        query = client.table(table).insert(
            rows, returning="representation" if returning else "minimal"
        )
        return query.retry(False).execute().data

    return writer.write(step, send)


def update(
    writer: Writer, step: str, table: str, values: dict, *, eq: dict,
    in_: dict | None = None,
) -> None:
    """Update the rows matching every eq pair and in_ list, in one request, sent once."""
    def send(client):
        query = client.table(table).update(values, returning="minimal")
        for column, value in eq.items():
            query = query.eq(column, value)
        for column, allowed in (in_ or {}).items():
            query = query.in_(column, allowed)
        return query.retry(False).execute().data

    writer.write(step, send)


def dataset_name_ok(name: str) -> bool:
    return bool(SAFE_DATASET_NAME.fullmatch(name)) and not DOTS_ONLY.fullmatch(name)


def pick_dataset(rows: list[dict], name: str) -> dict | None:
    """The live dataset with this name, if exactly one has it.

    Names match ignoring case and surrounding spaces, so "MYB41" and "myb41 " are one name:
    a loaded dataset's name cannot be taken again by a near copy.
    """
    wanted = name.strip()
    live = [
        r for r in rows
        if r.get("deleted_at") is None
        and (r.get("name") or "").strip().casefold() == wanted.casefold()
    ]
    if len(live) > 1:
        ids = ", ".join(str(r["id"]) for r in live)
        raise LoadError(
            f"{len(live)} datasets are named {wanted!r} (ids {ids}); cannot tell which to "
            "write to"
        )
    return live[0] if live else None


def find_dataset(writer: Writer, species_id: int, name: str) -> dict | None:
    """Look a dataset up by trimmed name; PostgREST cannot filter on btrim(name)."""
    rows = read_all(
        writer, "scrna_datasets", DATASET_COLUMNS,
        filters=[("eq", "species_id", species_id), ("is_", "deleted_at", "null")],
    )
    return pick_dataset(rows, name)
