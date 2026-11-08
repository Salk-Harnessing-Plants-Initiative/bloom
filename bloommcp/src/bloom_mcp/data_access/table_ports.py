"""Table-read port for bloommcp's general data tools, and its value types.

The data tools depend on :class:`BloomDataReader`, never on Supabase:
:class:`~bloom_mcp.data_access.table_reader.PostgrestTableReader` reads PostgREST as
bloom_agent; :class:`~bloom_mcp.data_access.fake_table_reader.FakeTableReader` is the
in-memory test double. The allow-list and limits live in the tools, not the adapters.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, Protocol

# The comparisons the data tools offer. `contains` is a case-insensitive substring match;
# `is_null` takes true (is null) or false (is not null).
FilterOp = Literal["eq", "neq", "gt", "gte", "lt", "lte", "in", "contains", "is_null"]

# bloom_agent's statement_timeout: the database cancels a longer query.
STATEMENT_TIMEOUT_SECONDS = 15


@dataclass(frozen=True)
class Filter:
    column: str
    op: FilterOp
    value: Any


@dataclass(frozen=True)
class Column:
    name: str
    type: str
    # "table.column" this column is a foreign key to, if any.
    references: str | None = None


@dataclass(frozen=True)
class Param:
    name: str
    type: str
    required: bool


@dataclass(frozen=True)
class Function:
    name: str
    params: tuple[Param, ...]


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


class ColumnsChanged(TableReadError):
    """A column in the read no longer exists: a migration changed the table."""


class RequestTooLong(TableReadError):
    """The request's URL (columns plus filters) is longer than the gateway accepts."""


class DatabaseUnavailable(TableReadError):
    """The database couldn't be reached."""


class BloomDataReader(Protocol):
    """Reads Bloom's tables as bloom_agent."""

    def table_columns(self) -> dict[str, list[Column]]:
        """Every table and view the API exposes, with its columns in order."""
        ...

    def refresh_columns(self) -> bool:
        """Reread the columns now, unless that was tried moments ago; True if reread."""
        ...

    def functions(self) -> dict[str, Function]:
        """Every database function the API exposes, with its parameters."""
        ...

    def call(
        self,
        function: str,
        args: dict[str, Any],
        order: list[Sort],
        limit: int,
        offset: int,
    ) -> list[dict[str, Any]]:
        """Rows `offset` to `offset + limit - 1` of the function's ordered result."""
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
