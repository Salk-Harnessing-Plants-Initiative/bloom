"""The write rules the dataset load runs under: each write sent once, a refusal as
unauthorised answered by one fresh sign-in, an unknown outcome waited out. No network: the
client and the clock are fakes."""

from __future__ import annotations

import httpx
import pytest
from postgrest.exceptions import APIError
from scrna_fake_db import FakeClient

from bloomctl.scrna import _writer
from bloomctl.scrna._writer import LoadError, Marker, Writer


def unauthorised():
    return APIError({"code": "PGRST303", "message": "JWT expired"})


def writer(tmp_path, clock=lambda: 1000.0):
    """A writer whose client is a placeholder, counting how often it signs in again."""
    renewals = []
    marker = Marker(tmp_path / "m.json", wait_s=300, clock=clock)
    w = Writer(object(), lambda: renewals.append(1) or object(), marker)
    return w, renewals, marker


def signed_in(tmp_path, client):
    return Writer(client, lambda: client, Marker(tmp_path / "m.json", wait_s=0))


# --- sending once -------------------------------------------------------------


def test_an_unauthorised_write_signs_in_once_and_is_sent_once_more(tmp_path):
    w, renewals, _ = writer(tmp_path)
    calls = []

    def send(client):
        calls.append(client)
        if len(calls) == 1:
            raise unauthorised()
        return "ok"

    assert w.write("insert cells 1", send) == "ok"
    assert (len(calls), len(renewals)) == (2, 1)
    assert calls[0] is not calls[1], "the second send uses the fresh client"


def test_a_second_refusal_stops_the_load(tmp_path):
    w, renewals, _ = writer(tmp_path)
    calls = []

    def send(client):
        calls.append(client)
        raise unauthorised()

    with pytest.raises(LoadError, match="insert cells 1"):
        w.write("insert cells 1", send)
    assert (len(calls), len(renewals)) == (2, 1)


@pytest.mark.parametrize("error", [
    httpx.ReadTimeout("timed out"),
    httpx.RemoteProtocolError("dropped"),
    APIError({"code": 504, "message": "JSON could not be generated"}),
], ids=["read-timeout", "dropped", "gateway-504"])
def test_a_write_with_an_unknown_outcome_stops_and_is_recorded(tmp_path, error):
    w, _, marker = writer(tmp_path)
    calls = []

    def send(client):
        calls.append(client)
        raise error

    with pytest.raises(LoadError) as exc:
        w.write("insert cells 7", send)
    assert "insert cells 7" in str(exc.value)
    assert "outcome is unknown" in str(exc.value)
    assert "run the same command again to continue" in str(exc.value)
    assert len(calls) == 1 and marker.path.exists()


@pytest.mark.parametrize("error", [
    APIError({"code": "23505", "message": "duplicate key"}),
    APIError({"code": "42501", "message": "permission denied"}),
    APIError({"code": "57014", "message": "canceling statement due to timeout"}),
    httpx.ConnectError("refused"),
], ids=["duplicate", "permission", "statement-cancelled", "never-connected"])
def test_a_write_that_failed_outright_stops_without_a_record(tmp_path, error):
    w, _, marker = writer(tmp_path)
    calls = []

    def send(client):
        calls.append(client)
        raise error

    with pytest.raises(LoadError, match="run the same command again to continue"):
        w.write("insert cells 7", send)
    assert len(calls) == 1 and not marker.path.exists()


def test_a_fault_here_propagates_and_records_nothing(tmp_path):
    w, renewals, marker = writer(tmp_path)

    def send(client):
        raise KeyError("n_cells")

    with pytest.raises(KeyError):
        w.write("finish the dataset", send)
    assert renewals == [] and not marker.path.exists()


def test_a_read_refused_as_unauthorised_signs_in_once_and_reads_again(tmp_path):
    client = FakeClient({"t": [{"id": 1}]})
    client.fail(lambda op, table, payload: op == "select", unauthorised())
    renewed = []
    w = Writer(client, lambda: renewed.append(1) or client, Marker(tmp_path / "m.json", wait_s=0))
    assert _writer.read_all(w, "t", "id") == [{"id": 1}]
    assert renewed == [1]


def test_a_read_that_fails_otherwise_stops(tmp_path):
    client = FakeClient({"t": [{"id": 1}]})
    client.fail(lambda op, table, payload: op == "select", httpx.ReadTimeout("slow"))
    with pytest.raises(LoadError, match="reading failed"):
        _writer.read_all(signed_in(tmp_path, client), "t", "id")


# --- waiting out an unknown outcome --------------------------------------------


def test_a_rerun_inside_the_wait_is_refused_with_the_seconds_left(tmp_path):
    now = [1000.0]
    marker = Marker(tmp_path / "m.json", wait_s=300, clock=lambda: now[0])
    marker.record("insert cells 7")
    now[0] = 1100.0
    with pytest.raises(LoadError, match="200 seconds"):
        marker.check()


def test_a_rerun_after_the_wait_clears_the_record(tmp_path):
    now = [1000.0]
    marker = Marker(tmp_path / "m.json", wait_s=300, clock=lambda: now[0])
    marker.record("insert cells 7")
    now[0] = 1301.0
    marker.check()
    assert not marker.path.exists()


def test_the_record_is_kept_with_the_staged_uploads_named_for_the_dataset(tmp_path):
    path = _writer.marker_path(tmp_path, "MYB41 transgene")
    assert path.parent == tmp_path and "MYB41_transgene" in path.name


def test_the_wait_covers_the_statement_timeout_and_a_margin():
    assert Marker("m").wait_s == _writer.STATEMENT_TIMEOUT_S + _writer.WAIT_MARGIN_S


# --- finding the dataset -------------------------------------------------------


def rows(*items):
    return [{"id": i, "name": n, "deleted_at": d} for i, n, d in items]


def test_a_trimmed_name_matches():
    assert _writer.pick_dataset(rows((7, " MYB41 ", None)), "MYB41")["id"] == 7


def test_a_soft_deleted_dataset_is_ignored():
    assert _writer.pick_dataset(rows((7, "MYB41", "2026-01-01")), "MYB41") is None


def test_two_live_datasets_with_one_name_are_refused():
    with pytest.raises(LoadError, match=r"7.*9"):
        _writer.pick_dataset(rows((7, "MYB41", None), (9, "MYB41 ", None)), "MYB41")


def test_the_dataset_lookup_reads_the_species_datasets(tmp_path):
    client = FakeClient({"scrna_datasets": [
        {"id": 7, "name": " MYB41 ", "species_id": 1, "deleted_at": None},
        {"id": 8, "name": "MYB41", "species_id": 2, "deleted_at": None},
    ]})
    assert _writer.find_dataset(signed_in(tmp_path, client), 1, "MYB41")["id"] == 7


@pytest.mark.parametrize("name,ok", [
    ("MYB41 transgene", True), ("run_2.v1-a", True),
    ("a/b", False), ("..", False), ("x\\y", False), ("", False),
])
def test_a_dataset_name_must_fit_a_storage_path(name, ok):
    assert _writer.dataset_name_ok(name) is ok


# --- reading in pages, writing once ----------------------------------------------


def test_reads_are_paged_until_a_short_page(tmp_path):
    client = FakeClient({"t": [{"id": i, "v": i * 10} for i in range(12)]})
    got = _writer.read_all(signed_in(tmp_path, client), "t", "id,v", page=5)
    assert [r["id"] for r in got] == list(range(12))
    assert [e[0] for e in client.log] == ["select"] * 3


def test_reads_apply_their_filters(tmp_path):
    client = FakeClient({"t": [{"id": 1, "d": 7, "gone": None},
                               {"id": 2, "d": 7, "gone": "2026"},
                               {"id": 3, "d": 8, "gone": None}]})
    got = _writer.read_all(signed_in(tmp_path, client), "t", "id",
                           filters=[("eq", "d", 7), ("is_", "gone", "null")])
    assert got == [{"id": 1}]


def test_an_insert_is_sent_once_with_retries_off(tmp_path):
    client = FakeClient()
    _writer.insert(signed_in(tmp_path, client), "insert cells 1", "t", [{"a": 1}, {"a": 2}])
    assert client.log == [("insert", "t", 2, False)]
    assert [r["a"] for r in client.tables["t"]] == [1, 2]


def test_an_insert_can_return_what_it_wrote(tmp_path):
    client = FakeClient()
    (row,) = _writer.insert(signed_in(tmp_path, client), "register", "t", [{"a": 1}],
                            returning=True)
    assert row["a"] == 1 and "id" in row


def test_an_update_touches_only_matching_rows(tmp_path):
    client = FakeClient({"t": [{"id": 1, "v": 0}, {"id": 2, "v": 0}]})
    _writer.update(signed_in(tmp_path, client), "finish", "t", {"v": 9}, eq={"id": 2})
    assert [r["v"] for r in client.tables["t"]] == [0, 9]
    assert client.log[-1][3] is False


def test_update_can_narrow_by_a_list_of_values(tmp_path):
    client = FakeClient({"scrna_cells": [{"id": i, "dataset_id": 7, "cell_number": i,
                                          "facets": None} for i in range(4)]})
    _writer.update(signed_in(tmp_path, client), "label cells", "scrna_cells",
                   {"facets": {"t": "True"}}, eq={"dataset_id": 7}, in_={"cell_number": [1, 3]})
    assert [r["facets"] for r in client.tables["scrna_cells"]] == [
        None, {"t": "True"}, None, {"t": "True"}]
    assert client.log[-1][3] is False
