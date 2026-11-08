"""In-memory :class:`BloomDataReader` for the data tools' tests."""

from __future__ import annotations

import operator
from typing import Any

from .table_ports import Column, Filter, QueryTimedOut, Sort, TableReadError


def _matches(row: dict[str, Any], f: Filter) -> bool:
    value = row.get(f.column)
    if f.op == "is_null":
        return (value is None) == bool(f.value)
    if value is None:
        return False
    if f.op == "contains":
        return str(f.value).lower() in str(value).lower()
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
    ) -> None:
        self.tables = tables
        # {table: {column: "other_table.column"}}: the fake's foreign keys.
        self.references = dict(references or {})
        self.estimates = dict(estimates or {})
        self.slow_tables: set[str] = set()
        self.fail_with: TableReadError | None = None
        # The planner's guess for a filtered count: positive even when nothing matches.
        self.filtered_guess = FILTERED_GUESS
        self.calls: list[str] = []
        self.counts: list[tuple[str, bool]] = []

    def table_columns(self) -> dict[str, list[Column]]:
        return {
            name: [
                Column(c, _guess_type(v), self.references.get(name, {}).get(c))
                for c, v in (rows[0] if rows else {}).items()
            ]
            for name, rows in self.tables.items()
        }

    def _filtered(self, table: str, filters: list[Filter]) -> list[dict[str, Any]]:
        return [r for r in self.tables[table] if all(_matches(r, f) for f in filters)]

    def select(self, table, columns, filters, order: list[Sort], limit, offset):
        self.calls.append(table)
        if self.fail_with:
            raise self.fail_with
        if table in self.slow_tables:
            raise QueryTimedOut("statement timeout")
        rows = self._filtered(table, filters)
        # Stable sorts, last key first; nulls last ascending and first descending, as Postgres.
        for s in reversed(order):
            rows = sorted(
                rows,
                key=lambda r, c=s.column: (r.get(c) is None, r.get(c)),
                reverse=s.descending,
            )
        return [{c: r.get(c) for c in columns} for r in rows[offset : offset + limit]]

    def count(self, table, column, filters, *, exact):
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
