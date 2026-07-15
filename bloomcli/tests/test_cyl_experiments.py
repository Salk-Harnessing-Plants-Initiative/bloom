"""bloomctl cyl experiments — list (row shaping, sort, json; mocked client)."""

import json

from click.testing import CliRunner

import bloomctl.cli as climod
import bloomctl.cyl.experiments as ex
from bloomctl.cli import cli

# Species deliberately out of order to prove the (species, name) sort.
EXPS = [
    {"id": 2, "name": "Alpha", "species": {"common_name": "Rice"}},
    {"id": 1, "name": "Beta", "species": {"common_name": "Canola"}},
    {"id": 3, "name": "Alpha", "species": {"common_name": "Canola"}},
]


def _patch_authed(monkeypatch):
    monkeypatch.setattr(climod, "_authed_client", lambda profile: object())


# --- row shaping ------------------------------------------------------------


def test_build_experiment_row():
    row = ex.build_experiment_row({"id": 7, "name": "Exp 7", "species": {"common_name": "Canola"}})
    assert row == ["Canola", "Exp 7", "7"]


def test_build_experiment_row_tolerates_null_species():
    assert ex.build_experiment_row({"id": 5, "name": "Exp 5", "species": None}) == ["", "Exp 5", "5"]


def test_build_experiment_record():
    rec = ex.build_experiment_record({"id": 7, "name": "Exp 7", "species": {"common_name": "Canola"}})
    assert rec == {"species": "Canola", "experiment": "Exp 7", "experiment_id": 7}


def test_sort_key_orders_species_then_name():
    ordered = sorted(EXPS, key=ex.experiment_sort_key)
    assert [e["id"] for e in ordered] == [3, 1, 2]  # Canola/Alpha, Canola/Beta, Rice/Alpha


# --- query ------------------------------------------------------------------


def test_fetch_experiments_builds_query():
    captured = {}

    class _Q:
        def select(self, sel):
            captured["select"] = sel
            return self

        def execute(self):
            return type("R", (), {"data": EXPS})()

    class _Client:
        def table(self, name):
            captured["table"] = name
            return _Q()

    out = ex.fetch_experiments(_Client())
    assert out == EXPS
    assert captured["table"] == "cyl_experiments"
    assert "species(*)" in captured["select"]


def test_fetch_experiments_empty():
    class _Client:
        def table(self, name):
            class _Q:
                def select(self, *a):
                    return self

                def execute(self):
                    return type("R", (), {"data": None})()

            return _Q()

    assert ex.fetch_experiments(_Client()) == []


# --- command ----------------------------------------------------------------


def test_list_renders_table(monkeypatch):
    _patch_authed(monkeypatch)
    monkeypatch.setattr(ex, "fetch_experiments", lambda client: EXPS)
    res = CliRunner().invoke(cli, ["cyl", "experiments", "list"])
    assert res.exit_code == 0, res.output
    assert "Experiments" in res.output  # table title
    assert "Canola" in res.output and "Rice" in res.output


def test_list_json_sorted(monkeypatch):
    _patch_authed(monkeypatch)
    monkeypatch.setattr(ex, "fetch_experiments", lambda client: EXPS)
    res = CliRunner().invoke(cli, ["cyl", "experiments", "list", "--json"])
    assert res.exit_code == 0, res.output
    payload = json.loads(res.output)
    assert [e["experiment_id"] for e in payload] == [3, 1, 2]  # sorted by species, then name
    assert payload[0] == {"species": "Canola", "experiment": "Alpha", "experiment_id": 3}


def test_list_empty(monkeypatch):
    _patch_authed(monkeypatch)
    monkeypatch.setattr(ex, "fetch_experiments", lambda client: [])
    res = CliRunner().invoke(cli, ["cyl", "experiments", "list"])
    assert res.exit_code == 0
    assert "No experiments found" in res.output


def test_list_empty_json(monkeypatch):
    _patch_authed(monkeypatch)
    monkeypatch.setattr(ex, "fetch_experiments", lambda client: [])
    res = CliRunner().invoke(cli, ["cyl", "experiments", "list", "--json"])
    assert res.exit_code == 0
    assert res.output.strip() == "[]"


# --- grouping ---------------------------------------------------------------


def test_experiments_grouped_under_cyl_not_top_level():
    root = CliRunner().invoke(cli, ["--help"])
    assert "experiments" not in root.output  # not a top-level command
    sub = CliRunner().invoke(cli, ["cyl", "experiments", "--help"])
    assert "list" in sub.output
    assert "experiments" in CliRunner().invoke(cli, ["cyl", "--help"]).output
