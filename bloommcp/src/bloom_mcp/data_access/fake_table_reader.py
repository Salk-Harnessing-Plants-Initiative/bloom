"""In-memory :class:`BloomDataReader` for the data tools' tests."""

from __future__ import annotations

import operator
from collections.abc import Callable
from typing import Any

from .table_ports import (
    Column,
    ColumnsChanged,
    Filter,
    Function,
    QueryTimedOut,
    Sort,
    TableReadError,
)


def _matches(row: dict[str, Any], f: Filter) -> bool:
    value = row.get(f.column)
    if f.op == "is_null":
        return (value is None) == bool(f.value)
    if value is None:
        return False
    if f.op == "contains":
        return str(f.value).lower() in str(value).lower()
    if f.op == "iequals":
        return str(value).lower() == str(f.value).lower()
    if f.op == "istarts_with":
        return str(value).lower().startswith(str(f.value).lower())
    if f.op == "in":
        return value in f.value
    compare = {
        "eq": operator.eq,
        "neq": operator.ne,
        "gt": operator.gt,
        "gte": operator.ge,
        "lt": operator.lt,
        "lte": operator.le,
    }[f.op]
    return compare(value, f.value)


def _sort_key(column: str) -> Callable[[dict[str, Any]], tuple[bool, Any]]:
    def key(row: dict[str, Any]) -> tuple[bool, Any]:
        return (row.get(column) is None, row.get(column))

    return key


FILTERED_GUESS = 70
# A column's SQL type, guessed from its first row's Python value.
_TYPES = (
    (bool, "boolean"),
    (int, "bigint"),
    (float, "double precision"),
    (dict, "jsonb"),
    (list, "jsonb"),
)


def _guess_type(value: Any) -> str:
    return next((t for kind, t in _TYPES if isinstance(value, kind)), "text")


class FakeTableReader:
    """Tables as lists of dicts. `slow_tables` raise QueryTimedOut on select and exact count;
    `fail_with`, when set, is raised by every select and count; `calls` records reads and
    `counts` records `(table, exact)` for each count."""

    def __init__(
        self,
        tables: dict[str, list[dict[str, Any]]],
        estimates: dict[str, int | None] | None = None,
        references: dict[str, dict[str, str]] | None = None,
        functions: dict[str, tuple[Function, list[dict[str, Any]]]] | None = None,
    ) -> None:
        self.tables = tables
        # {table: {column: "other_table.column"}}: the fake's foreign keys.
        self.references = dict(references or {})
        self.estimates = dict(estimates or {})
        self.slow_tables: set[str] = set()
        self.fail_with: TableReadError | None = None
        # The planner's guess for a filtered count: positive even when nothing matches.
        self.filtered_guess = FILTERED_GUESS
        # {name: (its parameters, the rows it returns)}; `called` records each call's args.
        self.function_rows = dict(functions or {})
        # function -> (limit input, its default): the function stops at that many rows.
        self.limit_inputs: dict[str, tuple[str, int]] = {}
        self.called: list[tuple[str, dict[str, Any]]] = []
        self.calls: list[str] = []
        self.counts: list[tuple[str, bool]] = []
        # Tables whose next read fails with ColumnsChanged, once; and refreshes asked for.
        self.changed_tables: set[str] = set()
        self.refreshes = 0
        self.may_refresh = True

    def table_columns(self) -> dict[str, list[Column]]:
        return {
            name: [
                Column(c, _guess_type(v), self.references.get(name, {}).get(c))
                for c, v in (rows[0] if rows else {}).items()
            ]
            for name, rows in self.tables.items()
        }

    def functions(self) -> dict[str, Function]:
        return {name: f for name, (f, _) in self.function_rows.items()}

    def call(
        self,
        function: str,
        args: dict[str, Any],
        order: list[Sort],
        limit: int,
        offset: int,
    ) -> list[dict[str, Any]]:
        self.called.append((function, args))
        if self.fail_with:
            raise self.fail_with
        rows = [dict(r) for r in self.function_rows[function][1]]
        for s in reversed(order):
            rows = sorted(rows, key=_sort_key(s.column), reverse=s.descending)
        # Like the real searches: no more rows than the function's own limit input.
        if function in self.limit_inputs:
            name, default = self.limit_inputs[function]
            rows = rows[: args.get(name, default)]
        return rows[offset : offset + limit]

    def refresh_columns(self) -> bool:
        self.refreshes += 1
        return self.may_refresh

    def _filtered(self, table: str, filters: list[Filter]) -> list[dict[str, Any]]:
        return [r for r in self.tables[table] if all(_matches(r, f) for f in filters)]

    def select(
        self,
        table: str,
        columns: list[str],
        filters: list[Filter],
        order: list[Sort],
        limit: int,
        offset: int,
    ) -> list[dict[str, Any]]:
        self.calls.append(table)
        if self.fail_with:
            raise self.fail_with
        if table in self.changed_tables:
            self.changed_tables.discard(table)
            raise ColumnsChanged("column dropped")
        if table in self.slow_tables:
            raise QueryTimedOut("statement timeout")
        rows = self._filtered(table, filters)
        # Stable sorts, last key first; nulls last ascending and first descending, as Postgres.
        for s in reversed(order):
            rows = sorted(rows, key=_sort_key(s.column), reverse=s.descending)
        return [{c: r.get(c) for c in columns} for r in rows[offset : offset + limit]]

    def count(
        self, table: str, column: str, filters: list[Filter], *, exact: bool
    ) -> int | None:
        self.calls.append(table)
        self.counts.append((table, exact))
        if self.fail_with:
            raise self.fail_with
        if exact and table in self.slow_tables:
            raise QueryTimedOut("statement timeout")
        if exact:
            return len(self._filtered(table, filters))
        if filters:
            return self.filtered_guess
        return self.estimates.get(table, len(self.tables[table]))
