"""Helpers for building PostgREST queries that stay within the gateway's limits.

A PostgREST `in.(…)` filter travels in the URL, not in a request body, so a long id list makes
a long address — and the gateway refuses one past a few kilobytes with 414 URI Too Long.
Measured against a dev stack: 1,312 small ids went through, 1,343 did not (~5.4 KB of list).

That ceiling is easy to walk into, because an id list is usually built from "however many rows
matched" rather than from a fixed number. Splitting the request is the fix.
"""

from __future__ import annotations

from typing import Any, Callable

# Kept comfortably under the ~5.4 KB measured ceiling, leaving room for the rest of the URL
# and for a stricter limit in front of production than in dev.
ID_FILTER_BUDGET_CHARS = 4000


def id_batches(ids: list[Any], budget: int = ID_FILTER_BUDGET_CHARS) -> list[list[Any]]:
    """Split ids into batches whose rendered `in.(…)` list stays under ``budget`` characters.

    Deliberately budgeted by characters rather than by a fixed count. Ids are bigints: today's
    are four digits, but the column allows nineteen, and 500 of those is a 10 KB filter. A
    count that is safe now would quietly stop being safe, which is exactly how this bug
    returns years later with nobody remembering why the number was what it was.

    A single id longer than the whole budget still gets its own batch — better one over-long
    request the server can refuse clearly than a silently dropped row.
    """
    batches: list[list[Any]] = []
    current: list[Any] = []
    length = 0
    for value in ids:
        rendered = len(str(value)) + 1  # +1 for the separating comma
        if current and length + rendered > budget:
            batches.append(current)
            current, length = [], 0
        current.append(value)
        length += rendered
    if current:
        batches.append(current)
    return batches


def fetch_in_batches(
    build: Callable[[list[Any]], Any],
    ids: list[Any],
    *,
    budget: int = ID_FILTER_BUDGET_CHARS,
) -> list[dict[str, Any]]:
    """Run one query per batch of ``ids`` and concatenate the rows, in batch order.

    ``build(batch)`` returns a query ready to execute for that batch — the caller keeps control
    of the whole chain, so filters that have to come after the `in_` (a soft-delete check, an
    order) still work.
    """
    rows: list[dict[str, Any]] = []
    for batch in id_batches(ids, budget):
        rows += build(batch).execute().data or []
    return rows


def queried(what: str, call: Callable[[], Any]) -> Any:
    """Run one metadata query, reporting a server or connection failure as a message naming
    ``what`` rather than as a traceback."""
    import click
    from postgrest import APIError

    try:
        return call()
    except APIError as exc:
        detail = getattr(exc, "message", None) or str(exc)
        raise click.ClickException(f"Could not read {what} from Bloom: {detail}") from exc
    except Exception as exc:
        if not _is_transport_error(exc):
            raise
        raise click.ClickException(
            f"Could not reach Bloom while reading {what} ({type(exc).__name__}) — "
            f"check your connection and retry"
        ) from exc


def _is_transport_error(exc: BaseException) -> bool:
    """True for a connection-level httpx failure, recognised by type because it often carries
    no message to match against."""
    try:
        import httpx

        return isinstance(exc, httpx.TransportError)
    except (ImportError, AttributeError):  # pragma: no cover - httpx ships with supabase
        return False
