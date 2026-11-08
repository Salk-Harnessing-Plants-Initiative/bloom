"""Table-read port for bloommcp's general data tools, and its value types.

The data tools depend on :class:`BloomDataReader`, never on Supabase:
:class:`~bloom_mcp.data_access.table_reader.PostgrestTableReader` reads PostgREST as
bloom_agent; :class:`~bloom_mcp.data_access.fake_table_reader.FakeTableReader` is the
in-memory test double. The allow-list and limits live in the tools, not the adapters.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

# The comparisons query_table offers. `contains` is a case-insensitive substring match;
# `is_null` takes true (is null) or false (is not null).
FILTER_OPS = ("eq", "neq", "gt", "gte", "lt", "lte", "in", "contains", "is_null")


@dataclass(frozen=True)
class Filter:
    column: str
    op: str
    value: Any


@dataclass(frozen=True)
class Sort:
    column: str
    descending: bool = False


class TableReadError(Exception):
    """A read failed; the message never carries hosts, SQL, database codes or internals."""


class QueryTimedOut(TableReadError):
    """The database cancelled the query at bloom_agent's statement time limit."""


class BadFilterValue(TableReadError):
    """A filter doesn't fit its column's type: a wrong kind of value, or contains on non-text."""


class DatabaseUnavailable(TableReadError):
    """The database couldn't be reached."""


class BloomDataReader(Protocol):
    """Reads Bloom's tables as bloom_agent."""

    def table_columns(self) -> dict[str, list[str]]:
        """Every table and view the API exposes, with its columns in order."""
        ...

    def select(
        self,
        table: str,
        columns: list[str],
        filters: list[Filter],
        order: list[Sort],
        limit: int,
        offset: int,
    ) -> list[dict[str, Any]]:
        """Rows `offset` to `offset + limit - 1` of the filtered, ordered table."""
        ...

    def count(
        self, table: str, column: str, filters: list[Filter], *, exact: bool
    ) -> int | None:
        """Exact row count, or the planner's estimate when `exact` is false.

        `column` is one the caller may read; a select of every column would fail on tables
        with column-level grants.
        """
        ...
