"""The reader's column cache: kept through a failed reread, reread at most every 30 s."""

from __future__ import annotations

import pytest

from bloom_mcp.data_access import table_reader
from bloom_mcp.data_access.table_ports import Column, DatabaseUnavailable
from bloom_mcp.data_access.table_reader import (
    COLUMNS_CACHE_SECONDS,
    COLUMNS_RETRY_SECONDS,
    PostgrestTableReader,
)


class _Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


@pytest.fixture
def clock(monkeypatch):
    c = _Clock()
    monkeypatch.setattr(table_reader.time, "monotonic", c)
    return c


@pytest.fixture
def reader(monkeypatch):
    r = PostgrestTableReader(client_factory=lambda: None)
    r.fetches = 0
    r.fail = False

    def fetch():
        r.fetches += 1
        if r.fail:
            raise DatabaseUnavailable("schema unreadable")
        return {"species": [Column("id", "bigint")] * r.fetches}, {}

    monkeypatch.setattr(r, "_fetch_schema", fetch)
    return r


def test_the_list_is_cached_for_five_minutes(reader, clock):
    reader.table_columns()
    clock.now += COLUMNS_CACHE_SECONDS - 1
    reader.table_columns()
    assert reader.fetches == 1
    clock.now += 2
    reader.table_columns()
    assert reader.fetches == 2


def test_a_failed_reread_keeps_the_last_good_list(reader, clock):
    first = reader.table_columns()
    reader.fail = True
    clock.now += COLUMNS_CACHE_SECONDS + 1
    assert reader.table_columns() == first


def test_after_a_failed_reread_the_next_try_waits_30_seconds(reader, clock):
    reader.table_columns()
    reader.fail = True
    clock.now += COLUMNS_CACHE_SECONDS + 1
    reader.table_columns()
    clock.now += COLUMNS_RETRY_SECONDS - 1
    reader.table_columns()
    assert reader.fetches == 2
    reader.fail = False
    clock.now += 1
    reader.table_columns()
    assert reader.fetches == 3


def test_with_no_list_yet_a_failure_is_raised(reader, clock):
    reader.fail = True
    with pytest.raises(DatabaseUnavailable):
        reader.table_columns()


def test_refresh_rereads_at_most_every_30_seconds(reader, clock):
    reader.table_columns()
    clock.now += COLUMNS_RETRY_SECONDS
    assert reader.refresh_columns() is True
    assert reader.refresh_columns() is False
    assert reader.fetches == 2
    clock.now += COLUMNS_RETRY_SECONDS
    assert reader.refresh_columns() is True
    assert len(reader.table_columns()["species"]) == 3
