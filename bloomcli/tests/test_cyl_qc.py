"""bloomctl cyl qc — list-sets (shaping, code count, sort, json; mocked client)."""

import json

from click.testing import CliRunner

import bloomctl.cli as climod
import bloomctl.cyl.qc as qc
from bloomctl.cli import cli

# cyl_qc_sets rows as PostgREST returns them (experiment+species embedded, codes as id list).
SETS = [
    {
        "id": 20,
        "name": "outliers",
        "cyl_experiments": {"id": 2, "name": "Rice Wave 2", "species": {"common_name": "Rice"}},
        "cyl_qc_codes": [{"id": 1}, {"id": 2}, {"id": 3}],
    },
    {
        "id": 10,
        "name": "bad-scans",
        "cyl_experiments": {"id": 1, "name": "Canola Exp 1", "species": {"common_name": "Canola"}},
        "cyl_qc_codes": [{"id": 9}],
    },
]


def _patch_authed(monkeypatch):
    monkeypatch.setattr(climod, "_authed_client", lambda profile: object())


# --- shaping / counts -------------------------------------------------------


def test_build_qc_set_row_counts_codes():
    row = qc.build_qc_set_row(SETS[0])
    assert row == ["outliers", "20", "Rice", "Rice Wave 2", "2", "3"]  # set id + 3 QC codes


def test_build_qc_set_row_tolerates_missing_relations():
    row = qc.build_qc_set_row({"name": "x", "cyl_experiments": None, "cyl_qc_codes": None})
    assert row == ["x", "", "", "", "", "0"]  # no id/experiment/species, zero codes


def test_build_qc_set_record():
    rec = qc.build_qc_set_record(SETS[1])
    assert rec == {
        "id": 10,
        "name": "bad-scans",
        "species": "Canola",
        "experiment": "Canola Exp 1",
        "experiment_id": 1,
        "qc_code_count": 1,
    }


def test_qc_set_sort_key_species_then_name():
    ordered = sorted(SETS, key=qc.qc_set_sort_key)
    assert [s["name"] for s in ordered] == ["bad-scans", "outliers"]  # Canola before Rice


# --- query shape ------------------------------------------------------------


def test_fetch_qc_sets_builds_query():
    captured = {}

    class _Q:
        def select(self, sel):
            captured["select"] = sel
            return self

        def execute(self):
            return type("R", (), {"data": SETS})()

    class _Client:
        def table(self, name):
            captured["table"] = name
            return _Q()

    out = qc.fetch_qc_sets(_Client())
    assert out == SETS
    assert captured["table"] == "cyl_qc_sets"
    assert "cyl_experiments(*, species(*))" in captured["select"]
    assert "cyl_qc_codes(id)" in captured["select"]


# --- command ----------------------------------------------------------------


def test_list_sets_json_sorted(monkeypatch):
    _patch_authed(monkeypatch)
    monkeypatch.setattr(qc, "fetch_qc_sets", lambda client: SETS)
    res = CliRunner().invoke(cli, ["cyl", "qc", "list-sets", "--json"])
    assert res.exit_code == 0, res.output
    payload = json.loads(res.output)
    assert [s["name"] for s in payload] == ["bad-scans", "outliers"]  # species-then-name
    assert payload[0]["qc_code_count"] == 1
    assert payload[1]["qc_code_count"] == 3


def test_list_sets_table(monkeypatch):
    _patch_authed(monkeypatch)
    monkeypatch.setattr(qc, "fetch_qc_sets", lambda client: SETS)
    res = CliRunner().invoke(cli, ["cyl", "qc", "list-sets"])
    assert res.exit_code == 0, res.output
    assert "QC sets" in res.output
    # names, set ids, and species all rendered — fails if a column is dropped/swapped
    for token in ("outliers", "bad-scans", "20", "10", "Rice", "Canola"):
        assert token in res.output, f"{token!r} missing from table output"


def test_list_sets_surfaces_api_error(monkeypatch):
    from postgrest import APIError

    _patch_authed(monkeypatch)

    def _boom(client):
        raise APIError({"message": "permission denied", "code": "42501"})

    monkeypatch.setattr(qc, "fetch_qc_sets", _boom)
    res = CliRunner().invoke(cli, ["cyl", "qc", "list-sets"])
    assert res.exit_code != 0
    assert "permission denied" in res.output  # clean message, not a raw traceback


def test_list_sets_api_error_without_message_falls_back(monkeypatch):
    # APIError.message is None when the payload has no "message" key; fall back to str(exc)
    # so the diagnostic survives instead of printing "Error: None".
    from postgrest import APIError

    _patch_authed(monkeypatch)

    def _boom(client):
        raise APIError({"code": "42501", "details": "insufficient privilege"})

    monkeypatch.setattr(qc, "fetch_qc_sets", _boom)
    res = CliRunner().invoke(cli, ["cyl", "qc", "list-sets"])
    assert res.exit_code != 0
    assert "None" not in res.output  # not "Error: None"
    assert "insufficient privilege" in res.output


def test_list_sets_empty(monkeypatch):
    _patch_authed(monkeypatch)
    monkeypatch.setattr(qc, "fetch_qc_sets", lambda client: [])
    res = CliRunner().invoke(cli, ["cyl", "qc", "list-sets"])
    assert res.exit_code == 0
    assert "No QC sets found" in res.output


# --- grouping ---------------------------------------------------------------


def test_qc_grouped_under_cyl():
    # qc is a subgroup of cyl, with list-sets inside it
    sub = CliRunner().invoke(cli, ["cyl", "qc", "--help"])
    assert "list-sets" in sub.output
    assert "qc" in CliRunner().invoke(cli, ["cyl", "--help"]).output
