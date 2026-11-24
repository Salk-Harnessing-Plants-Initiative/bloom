"""A PostgREST-shaped fake for the scrna export tests.

The exports page, order and count, and none of that can be exercised against the fakes the
other scrna tests use: `test_scrna_cli._Query` answers one table and cannot sort, page, filter
by threshold or count, and `scrna_fixtures` only writes h5ad files.

Two behaviours here exist to make a wrong implementation fail rather than pass:

* rows are served **by the range asked for**, so a read whose offset does not advance returns
  the same rows again instead of quietly working;
* NULLs sort where Postgres puts them — first on a descending order, last on ascending —
  unless the caller asks otherwise, so an export that forgets `nullsfirst=False` orders
  differently here too.
"""

from __future__ import annotations

from typing import Any


class WriteAttempted(AssertionError):
    """A read-only command tried to write."""


class Response:
    def __init__(self, data: list[dict[str, Any]], count: int | None = None) -> None:
        self.data = data
        self.count = count


class Query:
    """One table's rows, narrowed by the calls made against it."""

    def __init__(self, table: str, rows: list[dict[str, Any]], log: "Log") -> None:
        self._table = table
        self._rows = rows
        self._log = log
        self._orders: list[tuple[str, bool, bool]] = []
        self._count = None
        self._range: tuple[int, int] | None = None

    # --- narrowing -------------------------------------------------------------

    def select(self, _columns: str = "*", count: str | None = None) -> "Query":
        self._count = count
        return self

    def eq(self, column: str, value: Any) -> "Query":
        return self._narrowed([r for r in self._rows if str(r.get(column)) == str(value)])

    def neq(self, column: str, value: Any) -> "Query":
        return self._narrowed([r for r in self._rows if str(r.get(column)) != str(value)])

    def is_(self, column: str, value: str) -> "Query":
        assert value == "null"
        return self._narrowed([r for r in self._rows if r.get(column) is None])

    def not_is(self, column: str, value: str) -> "Query":
        assert value == "null"
        return self._narrowed([r for r in self._rows if r.get(column) is not None])

    def in_(self, column: str, values: list[Any]) -> "Query":
        self._log.in_filters.append(list(values))
        return self._narrowed([r for r in self._rows if r.get(column) in values])

    def lte(self, column: str, value: Any) -> "Query":
        kept = [r for r in self._rows if r.get(column) is not None and r[column] <= value]
        return self._narrowed(kept)

    def order(self, column: str, *, desc: bool = False, nullsfirst: bool | None = None) -> "Query":
        # Postgres puts NULLs first on DESC and last on ASC unless told otherwise.
        nulls_first = desc if nullsfirst is None else nullsfirst
        self._orders.append((column, desc, nulls_first))
        self._log.orders.append((column, desc, nulls_first))
        return self

    def range(self, start: int, end: int) -> "Query":
        self._range = (start, end)
        self._log.ranges.append((start, end))
        return self

    # --- answering -------------------------------------------------------------

    def execute(self) -> Response:
        rows = self._sorted(self._rows)
        total = len(rows)
        if self._range is not None:
            start, end = self._range
            page = rows[start : end + 1]
            cap = self._log.page_cap
            if cap is not None:
                page = page[:cap]
        else:
            page = rows
        if self._count == "exact":
            self._log.counts_requested += 1
        return Response(page, total if self._count == "exact" else None)

    # --- internals -------------------------------------------------------------

    def _narrowed(self, rows: list[dict[str, Any]]) -> "Query":
        narrowed = Query(self._table, rows, self._log)
        narrowed._orders = list(self._orders)
        narrowed._count = self._count
        narrowed._range = self._range
        return narrowed

    def _sorted(self, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        out = list(rows)
        for column, desc, nulls_first in reversed(self._orders):
            missing = 0 if nulls_first else 1
            out.sort(
                key=lambda r, c=column, m=missing: (
                    m if r.get(c) is None else (1 - m),
                    _Reversed(r.get(c)) if desc and r.get(c) is not None else r.get(c),
                ),
            )
        return out

    # --- writes are refused ------------------------------------------------------

    def _refuse(self, *_a: Any, **_k: Any) -> None:
        raise WriteAttempted(f"a read-only command wrote to {self._table}")

    insert = update = delete = upsert = _refuse


class _Reversed:
    """Sorts a value in reverse without needing a numeric negation."""

    __slots__ = ("value",)

    def __init__(self, value: Any) -> None:
        self.value = value

    def __lt__(self, other: "_Reversed") -> bool:
        return other.value < self.value

    def __eq__(self, other: object) -> bool:
        return isinstance(other, _Reversed) and other.value == self.value


class Log:
    """What the command asked for, so a test can assert on the request, not only the answer."""

    def __init__(self) -> None:
        self.orders: list[tuple[str, bool, bool]] = []
        self.ranges: list[tuple[int, int]] = []
        self.in_filters: list[list[Any]] = []
        self.counts_requested = 0
        self.page_cap: int | None = None


class FakeClient:
    """A client over a dict of tables. Unknown tables are an error, not an empty result."""

    def __init__(self, **tables: list[dict[str, Any]]) -> None:
        self.tables = {name: list(rows) for name, rows in tables.items()}
        self.log = Log()
        self.raises: dict[str, Exception] = {}

    def table(self, name: str) -> Query:
        if name in self.raises:
            raise self.raises[name]
        if name not in self.tables:
            raise AssertionError(f"the command read {name}, which this fake does not hold")
        return Query(name, self.tables[name], self.log)

    def rpc(self, *_a: Any, **_k: Any) -> None:
        raise WriteAttempted("a read-only command called an rpc")
