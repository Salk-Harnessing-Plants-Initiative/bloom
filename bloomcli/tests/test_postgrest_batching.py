"""Batching id filters so a PostgREST request can't exceed the gateway's URL limit.

A PostgREST `in.(…)` filter travels in the URL. Measured against a dev gateway: 1,312 small
ids returned 200, 1,343 returned 414 URI Too Long. Any query that builds its id list from
"however many rows matched" can walk into that, so the list gets split.
"""

from __future__ import annotations

from bloomctl._postgrest import ID_FILTER_BUDGET_CHARS, fetch_in_batches, id_batches


def _rendered(batch):
    """The filter text this batch would put in the URL."""
    return ",".join(map(str, batch))


def test_a_small_list_is_one_batch():
    assert id_batches([1, 2, 3]) == [[1, 2, 3]]


def test_an_empty_list_makes_no_batches():
    assert id_batches([]) == []


def test_a_long_list_is_split():
    batches = id_batches(list(range(1, 5001)))
    assert len(batches) > 1


def test_every_batch_stays_under_budget():
    for batch in id_batches(list(range(1, 20001))):
        assert len(_rendered(batch)) <= ID_FILTER_BUDGET_CHARS


def test_no_id_is_lost_duplicated_or_reordered():
    ids = list(range(1, 5001))
    assert [i for batch in id_batches(ids) for i in batch] == ids


def test_budget_is_by_characters_not_count():
    # The trap this avoids: a fixed count that is safe for 4-digit ids is not safe for the
    # 19-digit bigints the column allows — 500 of those is a 10 KB filter.
    narrow = id_batches(list(range(1000, 2000)))
    wide = id_batches([10**18 + i for i in range(1000)])
    assert len(wide) > len(narrow), "wide ids must pack fewer per request"
    for batch in wide:
        assert len(_rendered(batch)) <= ID_FILTER_BUDGET_CHARS


def test_an_id_larger_than_the_whole_budget_still_goes_out():
    # Better one over-long request the server refuses clearly than a silently dropped row.
    huge = "9" * (ID_FILTER_BUDGET_CHARS + 10)
    assert id_batches([huge]) == [[huge]]


def test_a_smaller_budget_makes_more_batches():
    assert len(id_batches(list(range(1, 1001)), budget=100)) > len(
        id_batches(list(range(1, 1001)), budget=1000)
    )


class _Query:
    def __init__(self, recorder, rows):
        self.recorder = recorder
        self.rows = rows
        self.batch = None

    def in_(self, column, values):
        self.batch = list(values)
        self.recorder.append((column, self.batch))
        return self

    def execute(self):
        return type("R", (), {"data": [{"id": i} for i in self.batch]})()


def test_fetch_in_batches_concatenates_every_batch_in_order():
    seen = []
    rows = fetch_in_batches(
        lambda batch: _Query(seen, None).in_("id", batch), list(range(1, 5001))
    )
    assert len(seen) > 1, "a 5,000-id list must not go out as one request"
    assert [r["id"] for r in rows] == list(range(1, 5001))


def test_fetch_in_batches_makes_no_request_for_no_ids():
    seen = []
    assert fetch_in_batches(lambda batch: _Query(seen, None).in_("id", batch), []) == []
    assert seen == []


def test_fetch_in_batches_lets_the_caller_finish_the_chain():
    # Real callers add filters after the in_ (a soft-delete check, an order), so the helper
    # must hand the batch to the caller rather than build the query itself.
    calls = []

    class _Chained(_Query):
        def is_(self, column, value):
            calls.append((column, value))
            return self

    fetch_in_batches(
        lambda batch: _Chained([], None).in_("id", batch).is_("deleted_at", "null"), [1, 2, 3]
    )
    assert calls == [("deleted_at", "null")]


# --- turning a query failure into a sentence ---------------------------------


def test_a_server_error_names_the_query_that_failed():
    import click
    import pytest
    from postgrest import APIError

    from bloomctl._postgrest import queried

    def denied():
        raise APIError({"message": "permission denied for view cyl_scans_extended"})

    with pytest.raises(click.ClickException) as caught:
        queried("this experiment's scans", denied)

    message = str(caught.value)
    assert "this experiment's scans" in message, "the message must say which read failed"
    assert "permission denied for view cyl_scans_extended" in message
    assert "Traceback" not in message


def test_a_read_timeout_is_a_sentence_too():
    """A TransportError carries no message at all, so it needs recognising by type."""
    import click
    import httpx
    import pytest

    from bloomctl._postgrest import queried

    def times_out():
        raise httpx.ReadTimeout("")

    with pytest.raises(click.ClickException) as caught:
        queried("the accession names", times_out)

    message = str(caught.value)
    assert "the accession names" in message
    assert "check your connection" in message


def test_an_unrelated_failure_is_left_alone():
    """Only server and transport failures are translated; a bug keeps its own traceback."""
    import pytest

    from bloomctl._postgrest import queried

    def has_a_bug():
        raise KeyError("scan_id")

    with pytest.raises(KeyError):
        queried("this scan", has_a_bug)


def test_a_successful_read_passes_its_value_through():
    from bloomctl._postgrest import queried

    assert queried("anything", lambda: [{"scan_id": 1}]) == [{"scan_id": 1}]
