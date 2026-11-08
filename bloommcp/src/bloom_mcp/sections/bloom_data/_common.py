"""Shared inputs and checks for the bloom_data tools."""

from __future__ import annotations

import functools
from collections.abc import Callable
from typing import Any, Literal, TypeVar

from pydantic import BaseModel, ConfigDict, Field

from bloom_mcp.contract.errors import BloomMCPError
from bloom_mcp.data_access import table_access
from bloom_mcp.data_access.table_ports import (
    BadFilterValue,
    DatabaseUnavailable,
    Filter,
    QueryTimedOut,
    TableReadError,
)
from bloom_mcp.tools import _ports

LIST_TABLES_REMEDY = "Call list_tables to see the readable tables and their columns."

_R = TypeVar("_R")


def fix_request(message: str, remedy: str = LIST_TABLES_REMEDY) -> BloomMCPError:
    """An error Claude can fix by changing its request."""
    return BloomMCPError(code="invalid_input", message=message, remedy=remedy)


def too_slow() -> BloomMCPError:
    return BloomMCPError(
        code="tool_error",
        message="The query took longer than the database allows (15 s); narrow it with a filter.",
        remedy="Add a filter on an id column, such as an experiment, species or scan id, "
        "or ask for fewer rows.",
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


def reports_read_errors(func: Callable[..., _R]) -> Callable[..., _R]:
    """Turns a failed read into read_failed's message; goes under @as_mcp_tool."""

    @functools.wraps(func)
    def wrapper(*args: Any, **kwargs: Any) -> _R:
        try:
            return func(*args, **kwargs)
        except TableReadError as exc:
            raise read_failed(exc) from None

    return wrapper


class FilterParam(BaseModel):
    model_config = ConfigDict(extra="forbid")

    column: str = Field(description="Column to compare.")
    op: Literal["eq", "neq", "gt", "gte", "lt", "lte", "in", "contains", "is_null"] = (
        Field(
            description="eq, neq, gt, gte, lt, lte; in (value is a list); contains (case-insensitive "
            "text match); is_null (value true for empty, false for not empty)."
        )
    )
    value: Any = Field(description="Value to compare with.")


def readable_columns(table: str) -> list[str]:
    """The table's columns Claude may see; raises if the table isn't on the allow-list."""
    if not table_access.is_readable(table):
        raise fix_request(f"'{table}' is not a table Bloom's data tools can read.")
    columns = _ports.table_reader().table_columns().get(table)
    if columns is None:
        raise fix_request(f"'{table}' is not a table Bloom's data tools can read.")
    return table_access.visible_columns(table, columns)


def check_columns(table: str, wanted: list[str], allowed: list[str]) -> None:
    unknown = [c for c in wanted if c not in allowed]
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


def to_filters(params: list[FilterParam]) -> list[Filter]:
    for p in params:
        if p.op == "in" and not isinstance(p.value, list):
            raise fix_request(
                f"Filter 'in' on '{p.column}' needs a list of values.", "Pass a list."
            )
        if p.op == "is_null" and not isinstance(p.value, bool):
            raise fix_request(
                f"Filter 'is_null' on '{p.column}' needs true or false.",
                "Pass true or false.",
            )
    return [Filter(p.column, p.op, p.value) for p in params]
