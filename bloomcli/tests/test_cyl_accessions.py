"""bloomctl cyl accessions — list + sample-counts (shaping, sort, json; mocked client)."""

import json

from click.testing import CliRunner

import bloomctl.cli as climod
import bloomctl.cyl.accessions as acc
from bloomctl.cli import cli

# cyl_experiment_accessions rows (out of order, to prove the name sort).
EXP_ACC = [
    {"accession_id": 9, "accession_name": "Col-0"},
    {"accession_id": 4, "accession_name": "Bay-0"},
]

# cyl_accession_sample_counts rows (out of order, to prove species-then-name sort).
COUNTS = [
    {"species_name": "Rice", "accession_id": 1, "accession_name": "IR64", "plant_count": 12},
    {"species_name": "Canola", "accession_id": 4, "accession_name": "Bay-0", "plant_count": 5},
    {"species_name": "Canola", "accession_id": 2, "accession_name": "Ames", "plant_count": 8},
]


def _patch_authed(monkeypatch):
    monkeypatch.setattr(climod, "_authed_client", lambda profile: object())


# --- shaping / sort ---------------------------------------------------------


def test_build_accession_row():
    assert acc.build_accession_row({"accession_id": 9, "accession_name": "Col-0"}) == ["Col-0", "9"]


def test_build_accession_row_tolerates_nulls():
    assert acc.build_accession_row({"accession_id": None, "accession_name": None}) == ["", ""]


def test_build_accession_record():
    assert acc.build_accession_record({"accession_id": 9, "accession_name": "Col-0"}) == {
        "accession_id": 9,
        "accession_name": "Col-0",
    }


def test_build_sample_count_row():
    row = acc.build_sample_count_row(
        {"species_name": "Canola", "accession_name": "Bay-0", "plant_count": 5}
    )
    assert row == ["Canola", "Bay-0", "5"]


def test_build_sample_count_record():
    rec = acc.build_sample_count_record(
        {"species_name": "Canola", "accession_name": "Bay-0", "plant_count": 5}
    )
    assert rec == {"species": "Canola", "accession": "Bay-0", "plant_count": 5}


def test_sample_count_sort_key_species_then_name():
    ordered = sorted(COUNTS, key=acc.sample_count_sort_key)
    assert [(r["species_name"], r["accession_name"]) for r in ordered] == [
        ("Canola", "Ames"),
        ("Canola", "Bay-0"),
        ("Rice", "IR64"),
    ]


# --- query shape ------------------------------------------------------------


def test_fetch_experiment_accessions_builds_query():
    captured = {}

    class _Q:
        def select(self, sel):
            captured["select"] = sel
            return self

        def eq(self, col, val):
            captured["eq"] = (col, val)
            return self

        def execute(self):
            return type("R", (), {"data": EXP_ACC})()

    class _Client:
        def table(self, name):
            captured["table"] = name
            return _Q()

    out = acc.fetch_experiment_accessions(_Client(), 7)
    assert out == EXP_ACC
    assert captured["table"] == "cyl_experiment_accessions"
    assert captured["eq"] == ("experiment_id", 7)
    assert "accession_name" in captured["select"]


def test_fetch_sample_counts_species_filter():
    captured = {"eq": None}

    class _Q:
        def select(self, sel):
            captured["select"] = sel
            return self

        def eq(self, col, val):
            captured["eq"] = (col, val)
            return self

        def execute(self):
            return type("R", (), {"data": COUNTS})()

    class _Client:
        def table(self, name):
            captured["table"] = name
            return _Q()

    # with a species filter → an .eq() on species_name
    acc.fetch_accession_sample_counts(_Client(), "Canola")
    assert captured["table"] == "cyl_accession_sample_counts"
    assert captured["eq"] == ("species_name", "Canola")


def test_fetch_sample_counts_no_filter_omits_eq():
    captured = {"eq": None}

    class _Q:
        def select(self, sel):
            return self

        def eq(self, col, val):
            captured["eq"] = (col, val)
            return self

        def execute(self):
            return type("R", (), {"data": COUNTS})()

    class _Client:
        def table(self, name):
            return _Q()

    acc.fetch_accession_sample_counts(_Client(), None)
    assert captured["eq"] is None  # no filter → no .eq() call


# --- commands ---------------------------------------------------------------


def test_list_requires_experiment_id(monkeypatch):
    _patch_authed(monkeypatch)
    res = CliRunner().invoke(cli, ["cyl", "accessions", "list"])
    assert res.exit_code != 0
    assert "experiment-id" in res.output.lower() or "experiment_id" in res.output.lower()


def test_list_json_sorted(monkeypatch):
    _patch_authed(monkeypatch)
    monkeypatch.setattr(acc, "fetch_experiment_accessions", lambda client, eid: EXP_ACC)
    res = CliRunner().invoke(cli, ["cyl", "accessions", "list", "--experiment-id", "7", "--json"])
    assert res.exit_code == 0, res.output
    payload = json.loads(res.output)
    assert [r["accession_name"] for r in payload] == ["Bay-0", "Col-0"]  # sorted by name


def test_list_empty(monkeypatch):
    _patch_authed(monkeypatch)
    monkeypatch.setattr(acc, "fetch_experiment_accessions", lambda client, eid: [])
    res = CliRunner().invoke(cli, ["cyl", "accessions", "list", "--experiment-id", "7"])
    assert res.exit_code == 0
    assert "No accessions found" in res.output


def test_sample_counts_json_sorted(monkeypatch):
    _patch_authed(monkeypatch)
    monkeypatch.setattr(acc, "fetch_accession_sample_counts", lambda client, species=None: COUNTS)
    res = CliRunner().invoke(cli, ["cyl", "accessions", "sample-counts", "--json"])
    assert res.exit_code == 0, res.output
    payload = json.loads(res.output)
    assert [(r["species"], r["accession"]) for r in payload] == [
        ("Canola", "Ames"),
        ("Canola", "Bay-0"),
        ("Rice", "IR64"),
    ]
    assert payload[0]["plant_count"] == 8


def test_sample_counts_table(monkeypatch):
    _patch_authed(monkeypatch)
    monkeypatch.setattr(acc, "fetch_accession_sample_counts", lambda client, species=None: COUNTS)
    res = CliRunner().invoke(cli, ["cyl", "accessions", "sample-counts"])
    assert res.exit_code == 0, res.output
    assert "Sample counts per accession" in res.output
    assert "Canola" in res.output and "Rice" in res.output


def test_sample_counts_empty(monkeypatch):
    _patch_authed(monkeypatch)
    monkeypatch.setattr(acc, "fetch_accession_sample_counts", lambda client, species=None: [])
    res = CliRunner().invoke(cli, ["cyl", "accessions", "sample-counts"])
    assert res.exit_code == 0
    assert "No sample counts found" in res.output


# --- grouping ---------------------------------------------------------------


def test_accessions_grouped_under_cyl_not_top_level():
    root = CliRunner().invoke(cli, ["--help"])
    assert "accessions" not in root.output  # not a top-level command
    sub = CliRunner().invoke(cli, ["cyl", "accessions", "--help"])
    assert "list" in sub.output
    assert "sample-counts" in sub.output
    assert "accessions" in CliRunner().invoke(cli, ["cyl", "--help"]).output


# --- edge cases / error handling -------------------------------------------


def test_build_sample_count_row_tolerates_null_count():
    # count(*) never yields NULL, but a null must render as "" not "None".
    row = acc.build_sample_count_row(
        {"species_name": "Rice", "accession_name": "IR64", "plant_count": None}
    )
    assert row == ["Rice", "IR64", ""]


def test_fetch_sample_counts_empty_species_omits_eq():
    captured = {"eq": None}

    class _Q:
        def select(self, sel):
            return self

        def eq(self, col, val):
            captured["eq"] = (col, val)
            return self

        def execute(self):
            return type("R", (), {"data": COUNTS})()

    class _Client:
        def table(self, name):
            return _Q()

    # empty --species means "no filter", not .eq("species_name", "")
    acc.fetch_accession_sample_counts(_Client(), "")
    assert captured["eq"] is None


def test_list_maps_apierror_to_clickexception(monkeypatch):
    from postgrest import APIError

    _patch_authed(monkeypatch)

    def _boom(client, experiment_id):
        raise APIError({"message": "permission denied", "code": "42501"})

    monkeypatch.setattr(acc, "fetch_experiment_accessions", _boom)
    res = CliRunner().invoke(cli, ["cyl", "accessions", "list", "--experiment-id", "7"])
    assert res.exit_code != 0
    assert "permission denied" in res.output  # clean message, not a raw traceback


def test_sample_counts_maps_apierror_to_clickexception(monkeypatch):
    from postgrest import APIError

    _patch_authed(monkeypatch)

    def _boom(client, species=None):
        raise APIError({"message": "permission denied", "code": "42501"})

    monkeypatch.setattr(acc, "fetch_accession_sample_counts", _boom)
    res = CliRunner().invoke(cli, ["cyl", "accessions", "sample-counts"])
    assert res.exit_code != 0
    assert "permission denied" in res.output
