"""bloomctl cyl qc — list-sets (shaping, code count, output formats; mocked client)."""

import csv
import io
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
    assert row == ["outliers", "Rice", "Rice Wave 2", "2", "3"]  # 3 QC codes


def test_build_qc_set_row_tolerates_missing_relations():
    row = qc.build_qc_set_row({"name": "x", "cyl_experiments": None, "cyl_qc_codes": None})
    assert row == ["x", "", "", "", "0"]  # no experiment/species, zero codes


def test_build_qc_set_record():
    rec = qc.build_qc_set_record(SETS[1])
    assert rec == {
        "name": "bad-scans",
        "species": "Canola",
        "experiment": "Canola Exp 1",
        "experiment_id": 1,
        "qc_code_count": 1,
    }


def test_build_qc_set_record_tolerates_missing_relations():
    rec = qc.build_qc_set_record({"name": "x", "cyl_experiments": None, "cyl_qc_codes": None})
    assert rec == {
        "name": "x",
        "species": None,
        "experiment": None,
        "experiment_id": None,
        "qc_code_count": 0,
    }


def test_columns_match_legacy_exactly():
    """Five legacy columns, legacy header wording, no additions."""
    assert qc.QC_SET_COLUMNS == [
        "QC Set Name",
        "Species",
        "Experiment Name",
        "Experiment ID",
        "Number of QC Codes",
    ]


def test_qc_set_fields_are_the_machine_contract():
    # the json/csv field names scripts depend on — pin them so a rename can't drift silently
    assert qc.QC_SET_FIELDS == ["name", "species", "experiment", "experiment_id", "qc_code_count"]


# --- query shape ------------------------------------------------------------


def test_fetch_qc_sets_builds_query():
    captured = {}

    class _Q:
        def select(self, sel):
            captured["select"] = sel
            return self

        def is_(self, col, val):
            captured["is_"] = (col, val)
            return self

        def order(self, col):
            captured["order"] = col
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
    # inner-join so the soft-delete filter drops sets on tombstoned experiments
    assert "cyl_experiments!inner(*, species(*))" in captured["select"]
    assert "cyl_qc_codes(id)" in captured["select"]
    assert captured["is_"] == ("cyl_experiments.deleted_at", "null")  # exclude soft-deleted
    assert captured["order"] == "id"  # deterministic base fetch


# --- command ----------------------------------------------------------------


def test_list_sets_default_is_table(monkeypatch):
    """Bare invocation prints the human table, matching legacy."""
    _patch_authed(monkeypatch)
    monkeypatch.setattr(qc, "fetch_qc_sets", lambda client: SETS)
    res = CliRunner().invoke(cli, ["cyl", "qc", "list-sets"])
    assert res.exit_code == 0, res.output
    assert "QC Set Name" in res.output


def test_list_sets_output_json(monkeypatch):
    _patch_authed(monkeypatch)
    monkeypatch.setattr(qc, "fetch_qc_sets", lambda client: SETS)
    res = CliRunner().invoke(cli, ["cyl", "qc", "list-sets", "--output", "json"])
    assert res.exit_code == 0, res.output
    payload = json.loads(res.output)
    # Sorted deterministically (species, then experiment/name) regardless of fetch order:
    # fetch order is [outliers(Rice), bad-scans(Canola)] → output [bad-scans, outliers].
    assert [s["name"] for s in payload] == ["bad-scans", "outliers"]
    assert {s["name"]: s["qc_code_count"] for s in payload} == {"outliers": 3, "bad-scans": 1}
    assert all("id" not in s for s in payload)


def test_list_sets_output_csv(monkeypatch):
    _patch_authed(monkeypatch)
    monkeypatch.setattr(qc, "fetch_qc_sets", lambda client: SETS)
    res = CliRunner().invoke(cli, ["cyl", "qc", "list-sets", "--output", "csv"])
    assert res.exit_code == 0, res.output
    rows = list(csv.DictReader(io.StringIO(res.output)))
    assert {r["name"]: r["qc_code_count"] for r in rows} == {"outliers": "3", "bad-scans": "1"}


def test_list_sets_json_alias_equals_output_json(monkeypatch):
    # --json is an alias for --output json (parity with cyl experiments list)
    _patch_authed(monkeypatch)
    monkeypatch.setattr(qc, "fetch_qc_sets", lambda client: SETS)
    a = CliRunner().invoke(cli, ["cyl", "qc", "list-sets", "--output", "json"])
    b = CliRunner().invoke(cli, ["cyl", "qc", "list-sets", "--json"])
    assert a.exit_code == 0 and a.output == b.output


def test_list_sets_json_and_conflicting_output_rejected(monkeypatch):
    _patch_authed(monkeypatch)
    monkeypatch.setattr(qc, "fetch_qc_sets", lambda client: SETS)
    res = CliRunner().invoke(cli, ["cyl", "qc", "list-sets", "--json", "--output", "csv"])
    assert res.exit_code != 0
    assert "not both" in res.output.lower()


def test_list_sets_rejects_unknown_output(monkeypatch):
    _patch_authed(monkeypatch)
    monkeypatch.setattr(qc, "fetch_qc_sets", lambda client: SETS)
    res = CliRunner().invoke(cli, ["cyl", "qc", "list-sets", "--output", "yaml"])
    assert res.exit_code != 0
    assert "yaml" in res.output


def test_list_sets_empty_json_is_empty_array(monkeypatch):
    _patch_authed(monkeypatch)
    monkeypatch.setattr(qc, "fetch_qc_sets", lambda client: [])
    res = CliRunner().invoke(cli, ["cyl", "qc", "list-sets", "--output", "json"])
    assert res.exit_code == 0, res.output
    assert json.loads(res.output) == []


def test_list_sets_empty_csv_is_header_only(monkeypatch):
    _patch_authed(monkeypatch)
    monkeypatch.setattr(qc, "fetch_qc_sets", lambda client: [])
    res = CliRunner().invoke(cli, ["cyl", "qc", "list-sets", "--output", "csv"])
    assert res.exit_code == 0, res.output
    assert res.output.strip() == ",".join(qc.QC_SET_FIELDS)


def test_list_sets_table(monkeypatch):
    _patch_authed(monkeypatch)
    monkeypatch.setattr(qc, "fetch_qc_sets", lambda client: SETS)
    res = CliRunner().invoke(cli, ["cyl", "qc", "list-sets"])
    assert res.exit_code == 0, res.output
    assert "QC sets" in res.output
    # names, species, experiments and counts all rendered — fails if a column is dropped
    for token in ("outliers", "bad-scans", "Rice", "Canola", "Rice Wave 2", "Canola Exp 1"):
        assert token in res.output, f"{token!r} missing from table output"
    # the set id is deliberately not a column
    assert "QC Set ID" not in res.output


def test_list_sets_lists_a_set_with_no_codes(monkeypatch):
    """A set with zero QC codes reports 0 and is still listed, not filtered out."""
    empty_set = [
        {
            "id": 7,
            "name": "untouched",
            "cyl_experiments": {"id": 5, "name": "Wheat W1", "species": {"common_name": "Wheat"}},
            "cyl_qc_codes": [],
        }
    ]
    _patch_authed(monkeypatch)
    monkeypatch.setattr(qc, "fetch_qc_sets", lambda client: empty_set)
    res = CliRunner().invoke(cli, ["cyl", "qc", "list-sets", "--output", "json"])
    assert res.exit_code == 0, res.output
    payload = json.loads(res.output)
    assert payload == [
        {
            "name": "untouched",
            "species": "Wheat",
            "experiment": "Wheat W1",
            "experiment_id": 5,
            "qc_code_count": 0,
        }
    ]


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
