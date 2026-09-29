"""
Split PostgREST `in.(…)` id filters so each request stays under the gateway's URL
limit (bloom#901).

An `in.(…)` filter travels in the URL, so a long id list makes a long address, and
the gateway refuses one past a few kilobytes with 414 URI Too Long. Binary-searched
against the dev gateway for PR #650: 200 at 1,312 small ids, 414 at 1,343 (about
5.4 KB of id list).

Copied from bloomctl's `bloomcli/src/bloomctl/_postgrest.py` rather than imported:
this service doesn't depend on bloomctl (design D9 of add-cyl-pipeline-ui).
tests/test_postgrest_batches.py reads bloomctl's module to keep the two budgets
equal.
"""

from typing import Any

# Comfortably under the ~5.4 KB measured ceiling, leaving room for the rest of the
# URL and for a stricter limit in front of production than in dev.
ID_FILTER_BUDGET_CHARS = 4000


def id_batches(ids: list[Any], budget: int = ID_FILTER_BUDGET_CHARS) -> list[list[Any]]:
    """Split `ids` into batches whose rendered `in.(…)` list stays within `budget`
    characters, preserving order.

    Budgeted by characters rather than by a fixed count: today's scan ids are four
    digits, but the column is a bigint and allows nineteen. A single id longer than
    the whole budget still gets its own batch, so the server can refuse it clearly
    rather than the row being silently dropped.
    """
    batches: list[list[Any]] = []
    current: list[Any] = []
    length = 0
    for value in ids:
        rendered = len(str(value)) + 1  # +1 for the separating comma
        if current and length + rendered > budget:
            batches.append(current)
            current, length = [], 0
        current.append(value)
        length += rendered
    if current:
        batches.append(current)
    return batches
