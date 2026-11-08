"""One page of rows for Claude: capped, long values cut, and small enough for one reply."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from bloom_mcp.data_access import table_access

DEFAULT_LIMIT = 50
MAX_LIMIT = 500
# Longer text or JSON values are cut, so one row can't fill the reply.
MAX_CELL_CHARS = 2000
TRUNCATED = "…[cut]"
# Skipping rows makes Postgres walk them all; past this, filter on the key instead.
MAX_OFFSET = 10_000
# A page stops early once its rows reach this many characters, so one reply stays small.
MAX_REPLY_CHARS = 100_000


@dataclass(frozen=True)
class Page:
    rows: list[dict[str, Any]]
    has_more: bool
    truncated_values: bool
    cut_short: bool

    def next_offset(self, offset: int) -> int | None:
        return offset + len(self.rows) if self.has_more else None


def _cut(value: Any) -> tuple[Any, bool]:
    if isinstance(value, (dict, list)):
        text = json.dumps(value, default=str)
        return (
            (value, False)
            if len(text) <= MAX_CELL_CHARS
            else (text[:MAX_CELL_CHARS] + TRUNCATED, True)
        )
    if isinstance(value, str) and len(value) > MAX_CELL_CHARS:
        return value[:MAX_CELL_CHARS] + TRUNCATED, True
    return value, False


def fit_page(
    rows: list[dict[str, Any]],
    limit: int,
    redacted: frozenset[str] = frozenset(),
) -> Page:
    """`rows` holds up to limit + 1 rows: the extra one only says more exist."""
    has_more = len(rows) > limit
    truncated = cut_short = False
    page: list[dict[str, Any]] = []
    size = 0
    for row in rows[:limit]:
        for key, value in row.items():
            if key in redacted:
                value = table_access.redact_emails(value)
            row[key], cut = _cut(value)
            truncated = truncated or cut
        size += len(json.dumps(row, default=str))
        if page and size > MAX_REPLY_CHARS:
            has_more = cut_short = True
            break
        page.append(row)
    return Page(page, has_more, truncated, cut_short)
