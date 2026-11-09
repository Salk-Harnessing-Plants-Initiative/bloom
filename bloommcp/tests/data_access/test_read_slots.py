"""At most MAX_CONCURRENT_READS of Claude's reads hold a database connection at once."""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from postgrest.exceptions import APIError

from bloom_mcp.data_access import table_reader
from bloom_mcp.data_access.table_ports import DatabaseBusy, TableReadError
from bloom_mcp.sections.bloom_data._common import call_failed, read_failed


class _Query:
    """A query whose execute() blocks until released, counting how many run at once."""

    def __init__(self, gate: threading.Event, state: dict, fail: bool = False) -> None:
        self.gate, self.state, self.fail = gate, state, fail

    def execute(self):
        with self.state["lock"]:
            self.state["running"] += 1
            self.state["peak"] = max(self.state["peak"], self.state["running"])
        self.gate.wait(5)
        with self.state["lock"]:
            self.state["running"] -= 1
        if self.fail:
            raise APIError({"code": "42501", "message": "x", "details": "", "hint": ""})
        return "done"


@pytest.fixture
def state(monkeypatch):
    monkeypatch.setattr(
        table_reader,
        "_read_slots",
        threading.BoundedSemaphore(table_reader.MAX_CONCURRENT_READS),
    )
    monkeypatch.setattr(table_reader, "READ_SLOT_WAIT_SECONDS", 0.2)
    return {"lock": threading.Lock(), "running": 0, "peak": 0}


def test_no_more_than_the_limit_run_at_once(state):
    gate = threading.Event()
    with ThreadPoolExecutor(10) as pool:
        futures = [
            pool.submit(table_reader._execute, _Query(gate, state)) for _ in range(10)
        ]
        threading.Timer(0.1, gate.set).start()
        outcomes = []
        for f in futures:
            try:
                outcomes.append(f.result())
            except DatabaseBusy:
                outcomes.append("busy")
    assert state["peak"] == table_reader.MAX_CONCURRENT_READS
    assert "done" in outcomes


def test_a_read_that_waits_too_long_is_told_bloom_is_busy(state):
    gate = threading.Event()
    with ThreadPoolExecutor(table_reader.MAX_CONCURRENT_READS) as pool:
        held = [
            pool.submit(table_reader._execute, _Query(gate, state))
            for _ in range(table_reader.MAX_CONCURRENT_READS)
        ]
        while state["running"] < table_reader.MAX_CONCURRENT_READS:
            pass
        with pytest.raises(DatabaseBusy):
            table_reader._execute(_Query(gate, state))
        gate.set()
        assert [f.result() for f in held] == ["done"] * len(held)


def test_a_failed_read_frees_its_slot(state):
    gate = threading.Event()
    gate.set()
    for _ in range(table_reader.MAX_CONCURRENT_READS + 1):
        with pytest.raises(TableReadError):
            table_reader._execute(_Query(gate, state, fail=True))
    assert table_reader._execute(_Query(gate, state)) == "done"


@pytest.mark.parametrize("explain", [read_failed, call_failed])
def test_busy_is_reported_plainly(explain):
    err = explain(DatabaseBusy("no free read slot"))
    assert err.code == "tool_error"
    assert "busy" in err.message and "slot" not in err.message
