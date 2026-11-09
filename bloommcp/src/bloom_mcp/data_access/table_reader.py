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
    STATEMENT_TIMEOUT_SECONDS,
    BadFilterValue,
    Column,
    ColumnsChanged,
    DatabaseUnavailable,
    Filter,
    Function,
    FunctionRejected,
    Param,
    QueryTimedOut,
    RequestTooLong,
    Sort,
    TableReadError,
)

logger = logging.getLogger(__name__)

# Past bloom_agent's statement timeout, so the database's
# own cancellation (a clear 57014) arrives first.
CLIENT_TIMEOUT_SECONDS = STATEMENT_TIMEOUT_SECONDS + 5
STATEMENT_TIMEOUT_CODE = "57014"
# A filter value of the wrong type for its column (text, number, date or time), and an
# operator the column's type lacks (contains on a number).
BAD_FILTER_CODES = frozenset({"22P02", "22003", "22007", "22008", "42883"})
# A Bloom function's own RAISE: input checks we wrote, so their text can be passed on.
FUNCTION_RAISE_CODE = "P0001"
MAX_REASON_CHARS = 200
# A column named in the read doesn't exist.
UNDEFINED_COLUMN_CODE = "42703"
# The gateway's answer when the URL is over its limit (about 8 KB).
URI_TOO_LONG_CODE = "414"
# PostgREST's schema description; reread after this long so new tables appear.
COLUMNS_CACHE_SECONDS = 300
# The least time between two attempts to reread it, so failures and typos can't flood it.
COLUMNS_RETRY_SECONDS = 30
# How PostgREST's schema description marks a foreign key.
_FOREIGN_KEY = re.compile(r"<fk table='([^']+)' column='([^']+)'/>")


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


def _column(name: str, spec: dict[str, Any]) -> Column:
    """A column from PostgREST's schema description: its SQL type and any foreign key."""
    fk = _FOREIGN_KEY.search(spec.get("description", ""))
    return Column(
        name=name,
        type=spec.get("format") or spec.get("type") or "unknown",
        references=f"{fk[1]}.{fk[2]}" if fk else None,
    )


_RPC = "/rpc/"


def _function(name: str, spec: dict[str, Any]) -> Function:
    """A function from PostgREST's schema description: its parameters, from the POST body."""
    body: dict[str, Any] = next(
        (
            p
            for p in spec.get("post", {}).get("parameters", [])
            if p.get("in") == "body"
        ),
        {},
    )
    schema = body.get("schema", {})
    required = set(schema.get("required", []))
    return Function(
        name=name,
        params=tuple(
            Param(n, p.get("format") or p.get("type") or "unknown", n in required)
            for n, p in schema.get("properties", {}).items()
        ),
    )


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


def _function_reason(message: str | None) -> str:
    """A function's own message on one short line, without its leading 'name: '."""
    line = " ".join((message or "").split())
    line = re.sub(r"^\w+:\s*", "", line)
    if len(line) > MAX_REASON_CHARS:
        line = line[: MAX_REASON_CHARS - 1] + "…"
    return line or "The function refused these inputs."


def _execute(query: Any) -> Any:
    try:
        return query.execute()
    except APIError as exc:
        # A database error's code is text; a gateway's HTTP status arrives as a number.
        code = str(exc.code)
        if code == STATEMENT_TIMEOUT_CODE:
            raise QueryTimedOut("statement timeout") from None
        if code == FUNCTION_RAISE_CODE:
            raise FunctionRejected(_function_reason(exc.message)) from None
        logger.warning("bloom_data read failed: %s %s", exc.code, exc.message)
        if code == UNDEFINED_COLUMN_CODE:
            raise ColumnsChanged("column dropped") from None
        if code == URI_TOO_LONG_CODE:
            raise RequestTooLong("url too long") from None
        if code in BAD_FILTER_CODES:
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
        self._columns: dict[str, list[Column]] | None = None
        self._functions: dict[str, Function] = {}
        self._columns_at = 0.0
        self._tried_at = -float(COLUMNS_RETRY_SECONDS)

    def table_columns(self) -> dict[str, list[Column]]:
        now = time.monotonic()
        expired = now - self._columns_at > COLUMNS_CACHE_SECONDS
        if self._columns is None or (expired and self._may_retry(now)):
            self._reread(now)
        assert self._columns is not None
        return self._columns

    def refresh_columns(self) -> bool:
        now = time.monotonic()
        if not self._may_retry(now):
            return False
        self._reread(now)
        return True

    def functions(self) -> dict[str, Function]:
        self.table_columns()
        return self._functions

    def _may_retry(self, now: float) -> bool:
        return now - self._tried_at >= COLUMNS_RETRY_SECONDS

    def _reread(self, now: float) -> None:
        """Reread the columns; on failure keep the last good list, if there is one."""
        self._tried_at = now
        try:
            self._columns, self._functions = self._fetch_schema()
            self._columns_at = now
        except DatabaseUnavailable:
            if self._columns is None:
                raise
            logger.warning("bloom_data kept the last column list; reread failed")

    def _fetch_schema(self) -> tuple[dict[str, list[Column]], dict[str, Function]]:
        try:
            # PostgREST's root path serves its schema description, through the same session.
            response = self._client_factory().postgrest.session.get("")
            response.raise_for_status()
            schema = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            logger.warning(
                "bloom_data could not read the API schema: %s", type(exc).__name__
            )
            raise DatabaseUnavailable("schema unreadable") from None
        columns = {
            name: [_column(c, p) for c, p in spec.get("properties", {}).items()]
            for name, spec in schema.get("definitions", {}).items()
        }
        functions = {
            path.removeprefix(_RPC): _function(path.removeprefix(_RPC), spec)
            for path, spec in schema.get("paths", {}).items()
            if path.startswith(_RPC)
        }
        return columns, functions

    def select(
        self,
        table: str,
        columns: list[str],
        filters: list[Filter],
        order: list[Sort],
        limit: int,
        offset: int,
    ) -> list[dict[str, Any]]:
        query = self._client_factory().table(table).select(",".join(columns))
        for f in filters:
            query = _apply(query, f)
        for s in order:
            query = query.order(s.column, desc=s.descending)
        query = query.range(offset, offset + limit - 1)
        return _execute(query).data or []

    def call(
        self,
        function: str,
        args: dict[str, Any],
        order: list[Sort],
        limit: int,
        offset: int,
    ) -> list[dict[str, Any]]:
        # A POST: array arguments need a JSON body. PostgREST runs a STABLE function read-only.
        query = self._client_factory().rpc(function, args)
        for s in order:
            query = query.order(s.column, desc=s.descending)
        query = query.range(offset, offset + limit - 1)
        return _execute(query).data or []

    def count(
        self, table: str, column: str, filters: list[Filter], *, exact: bool
    ) -> int | None:
        # A GET of one row, not a HEAD: a HEAD's error has no body, so a timeout's code is lost.
        mode = CountMethod.exact if exact else CountMethod.planned
        query = self._client_factory().table(table).select(column, count=mode)
        for f in filters:
            query = _apply(query, f)
        return _execute(query.limit(1)).count
