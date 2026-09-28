"""The export fakes are only useful if they fail a wrong implementation.

Each test here pins a behaviour a later test depends on: serving rows by the range asked for,
Postgres's NULL placement, counting what the filters leave, and refusing writes. A fake that
quietly did the friendly thing instead would let a broken export pass.
"""

from __future__ import annotations

import pytest
from scrna_export_fakes import FakeClient, WriteAttempted


def _rows(n: int) -> list[dict]:
    return [{"id": i, "v": i} for i in range(n)]


def test_rows_are_served_by_the_range_asked_for():
    """An offset that does not advance must return the same rows, not the next ones."""
    client = FakeClient(t=_rows(10))
    first = client.table("t").select("*").range(0, 3).execute().data
    again = client.table("t").select("*").range(0, 3).execute().data
    second = client.table("t").select("*").range(4, 7).execute().data
    assert [r["id"] for r in first] == [0, 1, 2, 3]
    assert again == first
    assert [r["id"] for r in second] == [4, 5, 6, 7]


def test_a_page_cap_shortens_every_page():
    """The server may return fewer rows than asked for; a read that stops there sees one page."""
    client = FakeClient(t=_rows(10))
    client.log.page_cap = 2
    page = client.table("t").select("*").range(0, 4).execute().data
    assert [r["id"] for r in page] == [0, 1]


def test_nulls_sort_where_postgres_puts_them():
    """DESC puts NULLs first unless asked otherwise — the default an export must override."""
    rows = [{"id": 1, "v": 5}, {"id": 2, "v": None}, {"id": 3, "v": 9}]
    client = FakeClient(t=rows)

    descending = client.table("t").select("*").order("v", desc=True).execute().data
    assert [r["id"] for r in descending] == [2, 3, 1], "NULL should lead a DESC order"

    nulls_last = (
        client.table("t").select("*").order("v", desc=True, nullsfirst=False).execute().data
    )
    assert [r["id"] for r in nulls_last] == [3, 1, 2]

    ascending = client.table("t").select("*").order("v").execute().data
    assert [r["id"] for r in ascending] == [1, 3, 2], "NULL should trail an ASC order"


def test_orders_are_applied_in_the_order_they_were_asked():
    rows = [
        {"id": 1, "a": 1, "b": 2},
        {"id": 2, "a": 1, "b": 1},
        {"id": 3, "a": 0, "b": 9},
    ]
    client = FakeClient(t=rows)
    out = client.table("t").select("*").order("a").order("b").execute().data
    assert [r["id"] for r in out] == [3, 2, 1]


def test_an_exact_count_counts_what_the_filters_leave():
    """A count that ignored the filter would make every narrowed-export test vacuous."""
    rows = [{"id": i, "fdr": 0.01 if i < 3 else 0.9} for i in range(10)]
    client = FakeClient(t=rows)
    answer = client.table("t").select("*", count="exact").lte("fdr", 0.05).range(0, 99).execute()
    assert answer.count == 3
    assert len(answer.data) == 3


def test_a_count_is_only_given_when_it_is_asked_for():
    client = FakeClient(t=_rows(5))
    assert client.table("t").select("*").execute().count is None
    assert client.log.counts_requested == 0
    assert client.table("t").select("*", count="exact").execute().count == 5
    assert client.log.counts_requested == 1


def test_what_was_asked_is_recorded():
    client = FakeClient(t=_rows(5))
    client.table("t").select("*").order("id").range(0, 1).execute()
    assert client.log.orders == [("id", False, False)]
    assert client.log.ranges == [(0, 1)]


def test_a_table_the_fake_does_not_hold_is_an_error():
    """Returning an empty list would read as 'the dataset has nothing', which is a real state."""
    client = FakeClient(t=_rows(1))
    with pytest.raises(AssertionError, match="does not hold"):
        client.table("other")


@pytest.mark.parametrize("verb", ["insert", "update", "delete", "upsert"])
def test_a_write_is_refused(verb):
    client = FakeClient(t=_rows(1))
    with pytest.raises(WriteAttempted):
        getattr(client.table("t"), verb)({"id": 1})


def test_an_rpc_is_refused():
    client = FakeClient(t=_rows(1))
    with pytest.raises(WriteAttempted):
        client.rpc("anything")


def test_a_table_can_be_made_to_raise():
    """An expired session surfaces as an APIError from the table read, not over HTTP."""
    client = FakeClient(t=_rows(1))
    boom = RuntimeError("jwt expired")
    client.raises["t"] = boom
    with pytest.raises(RuntimeError, match="jwt expired"):
        client.table("t")
