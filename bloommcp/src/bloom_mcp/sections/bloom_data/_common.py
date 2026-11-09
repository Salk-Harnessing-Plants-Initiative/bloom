"""Shared inputs and checks for the bloom_data tools."""

from __future__ import annotations

import functools
import logging
import sys
import time
from collections.abc import Callable
from typing import Any, TypeVar

from pydantic import BaseModel, ConfigDict, Field

from bloom_mcp.contract.errors import BloomMCPError
from bloom_mcp.data_access import table_access
from bloom_mcp.data_access.table_ports import (
    STATEMENT_TIMEOUT_SECONDS,
    BadFilterValue,
    Column,
    ColumnsChanged,
    DatabaseBusy,
    DatabaseUnavailable,
    Filter,
    FilterOp,
    FunctionRejected,
    QueryTimedOut,
    RequestTooLong,
    TableReadError,
)
from bloom_mcp.tools import _ports

# One line per data-tool call on bloommcp's stdout (the container log): what Claude read,
# never filter values. bloommcp sets no logging config, so this logger prints itself.
usage_log = logging.getLogger("bloom_mcp.bloom_data.usage")
usage_log.setLevel(logging.INFO)
usage_log.propagate = False
if not usage_log.handlers:
    usage_log.addHandler(logging.StreamHandler(sys.stdout))

LIST_TABLES_REMEDY = "Call list_tables to see the readable tables and their columns."
LIST_FUNCTIONS_REMEDY = "Call list_functions to see the functions and their inputs."

_R = TypeVar("_R")
_Result = TypeVar("_Result", bound=BaseModel)


def fix_request(message: str, remedy: str = LIST_TABLES_REMEDY) -> BloomMCPError:
    """An error Claude can fix by changing its request."""
    return BloomMCPError(code="invalid_input", message=message, remedy=remedy)


def too_slow() -> BloomMCPError:
    return BloomMCPError(
        code="tool_error",
        message=f"The query took longer than the database allows ({STATEMENT_TIMEOUT_SECONDS} s); "
        "narrow it with a filter.",
        remedy="Add a filter on an id column, such as an experiment, species or scan id, "
        "or ask for fewer rows.",
    )


def call_failed(exc: TableReadError) -> BloomMCPError:
    """read_failed's wording for call_function, which has inputs, not filters."""
    if isinstance(exc, FunctionRejected):
        return fix_request(exc.reason, LIST_FUNCTIONS_REMEDY)
    if isinstance(exc, QueryTimedOut):
        return BloomMCPError(
            code="tool_error",
            message="The function took longer than the database allows "
            f"({STATEMENT_TIMEOUT_SECONDS} s).",
            remedy="Narrow it: fewer ids, a smaller match_count, or one experiment at a time.",
        )
    if isinstance(exc, BadFilterValue):
        return fix_request(
            "An input doesn't fit its type: text for a number, or a malformed date or id.",
            "Check the inputs' types with list_functions.",
        )
    if isinstance(
        exc, (ColumnsChanged, RequestTooLong, DatabaseBusy, DatabaseUnavailable)
    ):
        return read_failed(exc)
    return BloomMCPError(
        code="tool_error",
        message="The function couldn't run with these inputs.",
        remedy="Check its inputs with list_functions and retry; if it keeps failing, tell "
        "the user the Bloom team can look into it.",
    )


def read_failed(exc: TableReadError) -> BloomMCPError:
    """What Claude is told when a read fails: what to change, never the database's own error."""
    if isinstance(exc, QueryTimedOut):
        return too_slow()
    if isinstance(exc, BadFilterValue):
        return fix_request(
            "A filter doesn't fit its column: a value of the wrong kind (text for a number, "
            "a malformed date or id), or contains on a column that isn't text.",
            "Fix the filter's value, or use eq or in instead of contains on that column.",
        )
    if isinstance(exc, ColumnsChanged):
        return BloomMCPError(
            code="tool_error",
            message="The table's columns just changed.",
            remedy="Call list_tables with this table for its current columns, then retry.",
        )
    if isinstance(exc, RequestTooLong):
        return fix_request(
            "The request is too long to send: its filter values and columns don't fit "
            "in one call.",
            "Split a long in list into several calls, or ask for fewer columns.",
        )
    if isinstance(exc, DatabaseBusy):
        return BloomMCPError(
            code="tool_error",
            message="Bloom is busy with other reads right now.",
            remedy="Try again in a moment, and run reads one after another rather than "
            "many at once.",
        )
    if isinstance(exc, DatabaseUnavailable):
        return BloomMCPError(
            code="tool_error",
            message="Bloom's database couldn't be reached.",
            remedy="Try again in a moment.",
        )
    return BloomMCPError(
        code="tool_error",
        message="The database couldn't run this read.",
        remedy="Check the table, columns and filters with list_tables and retry; if it "
        "keeps failing, tell the user the Bloom team can look into it.",
    )


def _reports(
    explain: Callable[[TableReadError], BloomMCPError],
) -> Callable[[Callable[..., _R]], Callable[..., _R]]:
    def decorate(func: Callable[..., _R]) -> Callable[..., _R]:
        @functools.wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> _R:
            try:
                try:
                    return func(*args, **kwargs)
                except ColumnsChanged:
                    # A migration dropped a cached column: reread the columns, try once more.
                    if not _ports.table_reader().refresh_columns():
                        raise
                    return func(*args, **kwargs)
            except TableReadError as exc:
                raise explain(exc) from None

        return wrapper

    return decorate


# Turn a failed read into a plain message; they go under @as_mcp_tool.
reports_read_errors = _reports(read_failed)
reports_call_errors = _reports(call_failed)


class FilterParam(BaseModel):
    model_config = ConfigDict(extra="forbid")

    column: str = Field(description="Column to compare.")
    op: FilterOp = Field(
        description="eq, neq, gt, gte, lt, lte; in (value is a list); contains (case-insensitive "
        "text match, every character taken literally); is_null (value true for empty, false "
        "for not empty)."
    )
    value: Any = Field(description="Value to compare with.")


def readable_column_details(table: str) -> list[Column]:
    """The table's columns Claude may see; raises if the table isn't on the allow-list."""
    if not table_access.is_readable(table):
        raise fix_request(f"'{table}' is not a table Bloom's data tools can read.")
    reader = _ports.table_reader()
    columns = reader.table_columns().get(table)
    if columns is None and reader.refresh_columns():
        columns = reader.table_columns().get(table)
    if columns is None:
        raise fix_request(f"'{table}' is not a table Bloom's data tools can read.")
    visible = set(table_access.visible_columns(table, [c.name for c in columns]))
    return [c for c in columns if c.name in visible]


def readable_columns(table: str) -> list[str]:
    return [c.name for c in readable_column_details(table)]


def check_columns(table: str, wanted: list[str], allowed: list[str]) -> None:
    unknown = [c for c in wanted if c not in allowed]
    if unknown and _ports.table_reader().refresh_columns():
        # Perhaps a migration just added them.
        unknown = [c for c in wanted if c not in readable_columns(table)]
    if unknown:
        raise fix_request(
            f"'{table}' has no readable column {', '.join(repr(c) for c in unknown)}."
        )


def check_filterable(table: str, wanted: list[str]) -> None:
    blocked = [c for c in wanted if c in table_access.unfilterable_columns(table)]
    if blocked:
        raise fix_request(
            f"'{table}' can't be filtered or sorted on {', '.join(repr(c) for c in blocked)}.",
            "Read the column instead.",
        )


# One filter value: what eq, gt, contains and the members of an in list take.
_SCALARS = (str, int, float, bool)
# An in list goes into the request's URL; longer lists also make one slow query.
MAX_IN_VALUES = 200


def to_filters(params: list[FilterParam]) -> list[Filter]:
    for p in params:
        where = f"Filter '{p.op}' on '{p.column}'"
        if p.op == "is_null":
            if not isinstance(p.value, bool):
                raise fix_request(
                    f"{where} needs true or false.", "Pass true or false."
                )
        elif p.op == "in":
            if not isinstance(p.value, list):
                raise fix_request(f"{where} needs a list of values.", "Pass a list.")
            if len(p.value) > MAX_IN_VALUES:
                raise fix_request(
                    f"{where} has {len(p.value)} values; the most is {MAX_IN_VALUES}.",
                    f"Split the list into calls of up to {MAX_IN_VALUES} values.",
                )
            if not all(isinstance(v, _SCALARS) for v in p.value):
                raise fix_request(
                    f"{where} takes only text, numbers or true/false.",
                    _EMPTY_REMEDY,
                )
        elif p.value is None:
            raise fix_request(f"{where} can't compare with null.", _EMPTY_REMEDY)
        elif not isinstance(p.value, _SCALARS):
            raise fix_request(
                f"{where} needs one value: text, a number or true/false.",
                "Pass a single value; use op in for a list.",
            )
    return [Filter(p.column, p.op, p.value) for p in params]


_EMPTY_REMEDY = (
    "To find empty values use op is_null with true (or false for values that are set)."
)


def _rows(result: Any) -> int | None:
    for field in ("returned", "count"):
        if hasattr(result, field):
            return getattr(result, field)
    return len(result.tables) if hasattr(result, "tables") else None


def logs_call(func: Callable[..., _Result]) -> Callable[..., _Result]:
    """Logs one usage line per call; goes under @as_mcp_tool, above @reports_read_errors."""

    @functools.wraps(func)
    def wrapper(params: Any) -> _Result:
        started = time.monotonic()
        rows = chars = None
        outcome = "ok"
        try:
            result = func(params)
            rows, chars = _rows(result), len(result.model_dump_json())
            return result
        except BloomMCPError as exc:
            outcome = exc.code
            raise
        except Exception:
            outcome = "internal_error"
            raise
        finally:
            filters = ",".join(f.column for f in getattr(params, "filters", [])) or "-"
            usage_log.info(
                "bloom_data tool=%s table=%s filters=%s rows=%s chars=%s ms=%d outcome=%s",
                func.__name__,
                getattr(params, "table", None)
                or getattr(params, "function", None)
                or "-",
                filters,
                rows,
                chars,
                (time.monotonic() - started) * 1000,
                outcome,
            )

    return wrapper
