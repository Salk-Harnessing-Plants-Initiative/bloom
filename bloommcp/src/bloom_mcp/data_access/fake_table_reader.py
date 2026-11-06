"""In-memory :class:`BloomDataReader` for the data tools' tests."""

from __future__ import annotations

from typing import Any

from .table_ports import Filter, QueryTimedOut


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
    return {
        "eq": value == f.value,
        "neq": value != f.value,
        "gt": value > f.value,
        "gte": value >= f.value,
        "lt": value < f.value,
        "lte": value <= f.value,
    }[f.op]


class FakeTableReader:
    """Tables as lists of dicts. `slow_tables` raise QueryTimedOut on select and exact count;
    `calls` records reads and `counts` records `(table, exact)` for each count."""

    def __init__(
        self,
        tables: dict[str, list[dict[str, Any]]],
        estimates: dict[str, int | None] | None = None,
    ) -> None:
        self.tables = tables
        self.estimates = dict(estimates or {})
        self.slow_tables: set[str] = set()
        self.calls: list[str] = []
        self.counts: list[tuple[str, bool]] = []

    def table_columns(self) -> dict[str, list[str]]:
        return {
            name: list(rows[0]) if rows else [] for name, rows in self.tables.items()
        }

    def _filtered(self, table: str, filters: list[Filter]) -> list[dict[str, Any]]:
        return [r for r in self.tables[table] if all(_matches(r, f) for f in filters)]

    def select(self, table, columns, filters, order_by, descending, limit, offset):
        self.calls.append(table)
        if table in self.slow_tables:
            raise QueryTimedOut("statement timeout")
        rows = self._filtered(table, filters)
        if order_by:
            rows = sorted(
                rows,
                key=lambda r: (r.get(order_by) is None, r.get(order_by)),
                reverse=descending,
            )
        return [{c: r.get(c) for c in columns} for r in rows[offset : offset + limit]]

    def count(self, table, column, filters, *, exact):
        self.calls.append(table)
        self.counts.append((table, exact))
        if exact and table in self.slow_tables:
            raise QueryTimedOut("statement timeout")
        if not exact and not filters and table in self.estimates:
            return self.estimates[table]
        return len(self._filtered(table, filters))

    def row_estimates(self, tables):
        return {t: self.estimates.get(t) for t in tables}
