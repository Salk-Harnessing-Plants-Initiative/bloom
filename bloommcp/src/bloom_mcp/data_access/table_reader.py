"""PostgREST adapter for :class:`BloomDataReader`, reading as bloom_agent."""

from __future__ import annotations

import logging
import re
import time
from collections.abc import Callable
from typing import Any

import httpx
from postgrest.exceptions import APIError
from postgrest.types import CountMethod

from bloom_mcp import supabase_client

from .table_ports import (
    BadFilterValue,
    DatabaseUnavailable,
    Filter,
    QueryTimedOut,
    TableReadError,
)

logger = logging.getLogger(__name__)

# bloom_agent's statement_timeout is 15 s; the client waits a little longer so the database's
# own cancellation (a clear 57014) arrives first.
CLIENT_TIMEOUT_SECONDS = 20
STATEMENT_TIMEOUT_CODE = "57014"
# A filter value of the wrong type for its column (text, number, date or time), and an
# operator the column's type lacks (contains on a number).
BAD_FILTER_CODES = frozenset({"22P02", "22003", "22007", "22008", "42883"})
# PostgREST's schema description; reread after this long so new tables appear.
COLUMNS_CACHE_SECONDS = 300


def _regex_literal(value: Any) -> str:
    """A Postgres regex matching `value` itself. contains can't use ilike: PostgREST turns
    every `*` in a like pattern into a wildcard, escaped or not."""
    return re.sub(r"([^\w\s])", r"\\\1", str(value))


def _in_list(values: list[Any]) -> str:
    """PostgREST's in list, each value quoted with its quotes and backslashes escaped."""
    quoted = (
        '"' + str(v).replace("\\", "\\\\").replace('"', '\\"') + '"' for v in values
    )
    return f"({','.join(quoted)})"


def _apply(query: Any, f: Filter) -> Any:
    if f.op == "contains":
        return query.filter(f.column, "imatch", _regex_literal(f.value))
    if f.op == "in":
        return query.filter(f.column, "in", _in_list(list(f.value)))
    if f.op == "is_null":
        return (
            query.is_(f.column, "null") if f.value else query.not_.is_(f.column, "null")
        )
    return getattr(query, f.op)(f.column, f.value)


def _execute(query: Any) -> Any:
    try:
        return query.execute()
    except APIError as exc:
        if exc.code == STATEMENT_TIMEOUT_CODE:
            raise QueryTimedOut("statement timeout") from None
        logger.warning("bloom_data read failed: %s %s", exc.code, exc.message)
        if exc.code in BAD_FILTER_CODES:
            raise BadFilterValue("filter does not fit its column") from None
        raise TableReadError("read refused") from None
    except httpx.TimeoutException:
        raise QueryTimedOut("client timeout") from None
    except httpx.TransportError as exc:
        logger.warning(
            "bloom_data could not reach the database: %s", type(exc).__name__
        )
        raise DatabaseUnavailable("unreachable") from None


class PostgrestTableReader:
    """Reads through supabase-py's PostgREST client as bloom_agent."""

    def __init__(self, client_factory: Callable[[], Any] | None = None) -> None:
        self._client_factory = client_factory or (
            lambda: supabase_client.get_postgrest_client(
                timeout_seconds=CLIENT_TIMEOUT_SECONDS
            )
        )
        self._columns: dict[str, list[str]] | None = None
        self._columns_at = 0.0

    def table_columns(self) -> dict[str, list[str]]:
        now = time.monotonic()
        if self._columns is None or now - self._columns_at > COLUMNS_CACHE_SECONDS:
            self._columns = self._fetch_columns()
            self._columns_at = now
        return self._columns

    def _fetch_columns(self) -> dict[str, list[str]]:
        url, key = supabase_client.require_env()
        try:
            response = httpx.get(
                f"{url}/rest/v1/",
                headers={"apikey": key, "Authorization": f"Bearer {key}"},
                timeout=CLIENT_TIMEOUT_SECONDS,
            )
            response.raise_for_status()
            definitions = response.json().get("definitions", {})
        except (httpx.HTTPError, ValueError) as exc:
            logger.warning(
                "bloom_data could not read the API schema: %s", type(exc).__name__
            )
            raise DatabaseUnavailable("schema unreadable") from None
        return {
            name: list(spec.get("properties", {})) for name, spec in definitions.items()
        }

    def select(self, table, columns, filters, order, limit, offset):
        query = self._client_factory().table(table).select(",".join(columns))
        for f in filters:
            query = _apply(query, f)
        for s in order:
            query = query.order(s.column, desc=s.descending)
        query = query.range(offset, offset + limit - 1)
        return _execute(query).data or []

    def count(self, table, column, filters, *, exact):
        # A GET of one row, not a HEAD: a HEAD's error has no body, so a timeout's code is lost.
        mode = CountMethod.exact if exact else CountMethod.planned
        query = self._client_factory().table(table).select(column, count=mode)
        for f in filters:
            query = _apply(query, f)
        return _execute(query.limit(1)).count
