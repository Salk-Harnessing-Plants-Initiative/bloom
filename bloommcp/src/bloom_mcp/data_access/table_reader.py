"""PostgREST adapter for :class:`BloomDataReader`, reading as bloom_agent."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from typing import Any

import httpx
from postgrest.exceptions import APIError

from bloom_mcp import supabase_client

from .table_ports import Filter, QueryTimedOut, TableReadError

logger = logging.getLogger(__name__)

# bloom_agent's statement_timeout is 15 s; the client waits a little longer so the database's
# own cancellation (a clear 57014) arrives first.
CLIENT_TIMEOUT_SECONDS = 20
STATEMENT_TIMEOUT_CODE = "57014"
# PostgREST's schema description; reread after this long so new tables appear.
COLUMNS_CACHE_SECONDS = 300


def _like_pattern(value: Any) -> str:
    escaped = str(value).replace("\\", "\\\\").replace("%", r"\%").replace("_", r"\_")
    return f"%{escaped}%"


def _apply(query: Any, f: Filter) -> Any:
    if f.op == "contains":
        return query.ilike(f.column, _like_pattern(f.value))
    if f.op == "in":
        return query.in_(f.column, list(f.value))
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
        raise TableReadError(
            f"The database refused the read (code {exc.code})."
        ) from None
    except httpx.TimeoutException:
        raise QueryTimedOut("client timeout") from None


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
            raise TableReadError(
                "Could not read the list of tables; try again shortly."
            ) from None
        return {
            name: list(spec.get("properties", {})) for name, spec in definitions.items()
        }

    def select(self, table, columns, filters, order_by, descending, limit, offset):
        query = self._client_factory().table(table).select(",".join(columns))
        for f in filters:
            query = _apply(query, f)
        if order_by:
            query = query.order(order_by, desc=descending)
        query = query.range(offset, offset + limit - 1)
        return _execute(query).data or []

    def count(self, table, column, filters, *, exact):
        query = (
            self._client_factory()
            .table(table)
            .select(column, count="exact" if exact else "planned", head=True)
        )
        for f in filters:
            query = _apply(query, f)
        return _execute(query).count

    def row_estimates(self, tables):
        rows = (
            _execute(
                self._client_factory().rpc(
                    "bloom_table_row_estimates", {"p_tables": list(tables)}
                )
            ).data
            or []
        )
        found = {r["table_name"]: r["row_estimate"] for r in rows}
        return {t: found.get(t) for t in tables}
