"""
Unit tests for `scripts/scrna_ingest_api.py`, the sign-in and write rules the
single-cell loaders share. No network: the site, the client and the clock are fakes.
"""

from __future__ import annotations

import base64
import importlib.util
import json
import sys
from pathlib import Path

import httpx
import pytest
from postgrest.exceptions import APIError
from storage3.exceptions import StorageApiError

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SCRIPT = REPO_ROOT / "scripts" / "scrna_ingest_api.py"
SERVER = "https://staging.bloom.salk.edu:8443"


@pytest.fixture(scope="module")
def api():
    spec = importlib.util.spec_from_file_location("scrna_ingest_api", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules["scrna_ingest_api"] = module
    spec.loader.exec_module(module)
    return module


def token(role: str) -> str:
    """An unsigned JWT carrying only the claim the loaders read."""
    def part(obj):
        return base64.urlsafe_b64encode(json.dumps(obj).encode()).rstrip(b"=").decode()
    return f"{part({'alg': 'none'})}.{part({'role': role, 'sub': 'u1'})}.sig"


class FakeAuth:
    def __init__(self, role="bloom_writer", error=None):
        self.role, self.error, self.calls = role, error, 0

    def sign_in_with_password(self, credentials):
        self.calls += 1
        if self.error:
            raise self.error
        session = type("S", (), {"access_token": token(self.role)})()
        return type("R", (), {"session": session})()


def factory(auth):
    return lambda url, key: type("C", (), {"auth": auth})()


# --------------------------------------------------------------------------- #
# Finding the API
# --------------------------------------------------------------------------- #


def test_the_api_is_read_from_the_site(api):
    seen = []
    def get(url):
        seen.append(url)
        return json.dumps({"api_url": f"{SERVER}/api", "anon_key": "k"}).encode()
    assert api.resolve_api(SERVER + "/", None, None, get=get) == (f"{SERVER}/api", "k")
    assert seen == [f"{SERVER}/api/client-info"]


def test_overrides_skip_the_site(api):
    def get(url):
        raise AssertionError("fetched despite overrides")
    assert api.resolve_api(None, "http://x/api", "k", get=get) == ("http://x/api", "k")


@pytest.mark.parametrize("reply", [
    OSError("unreachable"), b"<html>401</html>", json.dumps({"api_url": "x"}).encode(),
], ids=["unreachable", "not-json", "missing-key"])
def test_a_site_that_cannot_provide_the_api_is_refused(api, reply):
    def get(url):
        if isinstance(reply, Exception):
            raise reply
        return reply
    with pytest.raises(api.IngestError) as exc:
        api.resolve_api(SERVER, None, None, get=get)
    assert f"{SERVER}/api/client-info" in str(exc.value)
    assert "--api-url" in str(exc.value)


def test_no_site_and_no_overrides_is_refused(api):
    with pytest.raises(api.IngestError, match="--server"):
        api.resolve_api(None, None, None)


# --------------------------------------------------------------------------- #
# Signing in
# --------------------------------------------------------------------------- #


def test_the_password_is_read_from_the_environment(api):
    assert api.read_password({"BLOOM_PASSWORD": "pw"}) == "pw"


def test_a_missing_password_is_refused_before_signing_in(api):
    with pytest.raises(api.IngestError, match="BLOOM_PASSWORD"):
        api.read_password({})


def test_a_wrong_password_names_the_account(api):
    from supabase_auth.errors import AuthApiError
    auth = FakeAuth(error=AuthApiError("Invalid login credentials", 400,
                                       "invalid_credentials"))
    with pytest.raises(api.IngestError, match="me@salk.edu"):
        api.sign_in("http://x/api", "k", "me@salk.edu", "pw",
                    create_client=factory(auth))


@pytest.mark.parametrize("role", ["bloom_writer", "bloom_admin"])
def test_a_writer_or_admin_may_load(api, role):
    session = api.sign_in("http://x/api", "k", "me@salk.edu", "pw",
                          create_client=factory(FakeAuth(role)))
    assert session.role == role


@pytest.mark.parametrize("role", ["bloom_user", "bloom_workflows", "authenticated"])
def test_an_account_without_write_access_is_refused(api, role):
    with pytest.raises(api.IngestError) as exc:
        api.sign_in("http://x/api", "k", "me@salk.edu", "pw",
                    create_client=factory(FakeAuth(role)))
    assert "me@salk.edu" in str(exc.value) and role in str(exc.value)


# --------------------------------------------------------------------------- #
# No write is sent twice
# --------------------------------------------------------------------------- #


def unauthorised():
    return APIError({"code": "PGRST303", "message": "JWT expired"})


class FakeSession:
    def __init__(self):
        self.client, self.renewals = object(), 0

    def renew(self):
        self.renewals += 1


def writer(api, tmp_path, clock=lambda: 1000.0):
    marker = api.Marker(tmp_path / "m.json", wait_s=300, clock=clock)
    session = FakeSession()
    return api.Writer(session, marker), session, marker


def test_an_unauthorised_write_signs_in_once_and_is_sent_once_more(api, tmp_path):
    w, session, _ = writer(api, tmp_path)
    calls = []
    def send(client):
        calls.append(client)
        if len(calls) == 1:
            raise unauthorised()
        return "ok"
    assert w.write("insert cells 1", send) == "ok"
    assert (len(calls), session.renewals) == (2, 1)


def test_a_second_refusal_stops_the_load(api, tmp_path):
    w, session, _ = writer(api, tmp_path)
    calls = []
    def send(client):
        calls.append(client)
        raise unauthorised()
    with pytest.raises(api.IngestError, match="insert cells 1"):
        w.write("insert cells 1", send)
    assert (len(calls), session.renewals) == (2, 1)


@pytest.mark.parametrize("error", [
    httpx.ReadTimeout("timed out"),
    httpx.RemoteProtocolError("dropped"),
    APIError({"code": 504, "message": "JSON could not be generated"}),
    StorageApiError("bad gateway", "InternalError", 502),
], ids=["read-timeout", "dropped", "gateway-504", "storage-502"])
def test_a_write_with_an_unknown_outcome_stops_and_is_recorded(api, tmp_path, error):
    w, _, marker = writer(api, tmp_path)
    calls = []
    def send(client):
        calls.append(client)
        raise error
    with pytest.raises(api.IngestError) as exc:
        w.write("insert genes 7", send)
    assert "insert genes 7" in str(exc.value)
    assert "re-run the same command to continue" in str(exc.value)
    assert len(calls) == 1 and marker.path.exists()


@pytest.mark.parametrize("error", [
    APIError({"code": "23505", "message": "duplicate key"}),
    APIError({"code": "42501", "message": "permission denied"}),
    APIError({"code": "57014", "message": "canceling statement due to timeout"}),
    httpx.ConnectError("refused"),
], ids=["duplicate", "permission", "statement-cancelled", "never-connected"])
def test_a_write_that_failed_outright_stops_without_a_record(api, tmp_path, error):
    w, _, marker = writer(api, tmp_path)
    calls = []
    def send(client):
        calls.append(client)
        raise error
    with pytest.raises(api.IngestError, match="re-run the same command to continue"):
        w.write("insert genes 7", send)
    assert len(calls) == 1 and not marker.path.exists()


def test_a_rerun_inside_the_wait_is_refused_with_the_seconds_left(api, tmp_path):
    now = [1000.0]
    marker = api.Marker(tmp_path / "m.json", wait_s=300, clock=lambda: now[0])
    marker.record("insert genes 7")
    now[0] = 1100.0
    with pytest.raises(api.IngestError, match="200 seconds"):
        marker.check()


def test_a_rerun_after_the_wait_clears_the_record(api, tmp_path):
    now = [1000.0]
    marker = api.Marker(tmp_path / "m.json", wait_s=300, clock=lambda: now[0])
    marker.record("insert genes 7")
    now[0] = 1301.0
    marker.check()
    assert not marker.path.exists()


def test_the_record_sits_beside_the_input_named_for_the_dataset(api, tmp_path):
    path = api.marker_path(tmp_path / "cells.h5ad", "MYB41 transgene")
    assert path.parent == tmp_path and "MYB41" in path.name


# --------------------------------------------------------------------------- #
# Finding the dataset
# --------------------------------------------------------------------------- #


def rows(*items):
    return [{"id": i, "name": n, "deleted_at": d} for i, n, d in items]


def test_a_trimmed_name_matches(api):
    assert api.pick_dataset(rows((7, " MYB41 ", None)), "MYB41")["id"] == 7


def test_a_soft_deleted_dataset_is_ignored(api):
    assert api.pick_dataset(rows((7, "MYB41", "2026-01-01")), "MYB41") is None


def test_two_live_datasets_with_one_name_are_refused(api):
    with pytest.raises(api.IngestError, match=r"7.*9"):
        api.pick_dataset(rows((7, "MYB41", None), (9, "MYB41 ", None)), "MYB41")


# --------------------------------------------------------------------------- #
# The session keeps who signed in; a fault in the loader is not an outage
# --------------------------------------------------------------------------- #


def test_the_session_keeps_the_user_id_from_the_token(api):
    auth = FakeAuth("bloom_writer")
    session = api.sign_in("http://x/api", "k", "me@salk.edu", "pw",
                          create_client=factory(auth))
    assert session.user_id == "u1"
    session.renew()
    assert session.user_id == "u1" and auth.calls == 2


def test_a_fault_in_the_loader_propagates_and_records_nothing(api, tmp_path):
    w, session, marker = writer(api, tmp_path)
    calls = []
    def send(client):
        calls.append(client)
        raise KeyError("n_group1")
    with pytest.raises(KeyError):
        w.write("insert comparisons", send)
    assert len(calls) == 1 and session.renewals == 0 and not marker.path.exists()


# --------------------------------------------------------------------------- #
# Reading in pages, writing once
# --------------------------------------------------------------------------- #


from tests.unit.fake_supabase import FakeClient  # noqa: E402


def signed_in(api, tmp_path, client):
    session = api.Session(lambda: (client, "bloom_writer", "u1"), client,
                          "bloom_writer", "u1")
    return api.Writer(session, api.Marker(tmp_path / "m.json", wait_s=0))


def test_reads_are_paged_until_a_short_page(api, tmp_path):
    client = FakeClient({"t": [{"id": i, "v": i * 10} for i in range(12)]})
    w = signed_in(api, tmp_path, client)
    rows = api.read_all(w, "t", "id,v", page=5)
    assert [r["id"] for r in rows] == list(range(12))
    assert [e[0] for e in client.log] == ["select"] * 3


def test_reads_apply_their_filters(api, tmp_path):
    client = FakeClient({"t": [{"id": 1, "d": 7, "gone": None},
                               {"id": 2, "d": 7, "gone": "2026"},
                               {"id": 3, "d": 8, "gone": None}]})
    w = signed_in(api, tmp_path, client)
    rows = api.read_all(w, "t", "id", filters=[("eq", "d", 7), ("is_", "gone", "null")])
    assert rows == [{"id": 1}]


def test_an_insert_is_sent_once_with_retries_off(api, tmp_path):
    client = FakeClient()
    w = signed_in(api, tmp_path, client)
    api.insert(w, "insert cells 1", "t", [{"a": 1}, {"a": 2}])
    assert client.log == [("insert", "t", 2, False)]
    assert [r["a"] for r in client.tables["t"]] == [1, 2]


def test_an_insert_can_return_what_it_wrote(api, tmp_path):
    client = FakeClient()
    w = signed_in(api, tmp_path, client)
    (row,) = api.insert(w, "register", "t", [{"a": 1}], returning=True)
    assert row["a"] == 1 and "id" in row


def test_an_update_touches_only_matching_rows(api, tmp_path):
    client = FakeClient({"t": [{"id": 1, "v": 0}, {"id": 2, "v": 0}]})
    w = signed_in(api, tmp_path, client)
    api.update(w, "finish", "t", {"v": 9}, eq={"id": 2})
    assert [r["v"] for r in client.tables["t"]] == [0, 9]
    assert client.log[-1][3] is False


def test_a_read_refused_as_unauthorised_signs_in_once_and_reads_again(api, tmp_path):
    client = FakeClient({"t": [{"id": 1}]})
    client.fail(lambda op, table, payload: op == "select", unauthorised())
    renewed = []
    session = api.Session(lambda: (renewed.append(1) or client, "bloom_writer", "u1"),
                          client, "bloom_writer", "u1")
    w = api.Writer(session, api.Marker(tmp_path / "m.json", wait_s=0))
    assert api.read_all(w, "t", "id") == [{"id": 1}]
    assert renewed == [1]


def test_the_dataset_lookup_reads_the_species_datasets(api, tmp_path):
    client = FakeClient({"scrna_datasets": [
        {"id": 7, "name": " MYB41 ", "species_id": 1, "deleted_at": None},
        {"id": 8, "name": "MYB41", "species_id": 2, "deleted_at": None},
    ]})
    w = signed_in(api, tmp_path, client)
    assert api.find_dataset(w, 1, "MYB41")["id"] == 7


@pytest.mark.parametrize("name,ok", [
    ("MYB41 transgene", True), ("run_2.v1-a", True),
    ("a/b", False), ("..", False), ("x\\y", False), ("", False),
])
def test_a_dataset_name_must_fit_a_storage_path(api, name, ok):
    assert api.dataset_name_ok(name) is ok


def test_update_can_narrow_by_a_list_of_values(api, tmp_path):
    from tests.unit.fake_supabase import FakeClient

    client = FakeClient({"scrna_cells": [{"id": i, "dataset_id": 7, "cell_number": i,
                                          "facets": None} for i in range(4)]})
    w = api.Writer(api.Session(lambda: (client, "bloom_writer", "u1"), client,
                               "bloom_writer", "u1"), api.Marker(tmp_path / "m.json", wait_s=0))
    api.update(w, "label cells", "scrna_cells", {"facets": {"t": "True"}},
               eq={"dataset_id": 7}, in_={"cell_number": [1, 3]})
    assert [r["facets"] for r in client.tables["scrna_cells"]] == [
        None, {"t": "True"}, None, {"t": "True"}]
    assert client.log[-1][3] is False
