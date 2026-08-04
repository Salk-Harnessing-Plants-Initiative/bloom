"""bloomctl cyl datasets — list + create (row shaping, filters, RPC wiring; mocked client)."""

import json

from click.testing import CliRunner

import bloomctl.cli as climod
import bloomctl.cyl.datasets as ds
from bloomctl.cli import cli

# A fully-joined cyl_datasets row as PostgREST would return it.
FULL = {
    "name": "canola-v1",
    "timepoints": [1, 3, 5],
    "created_at": "2026-02-14T08:30:00+00:00",
    "cyl_experiments": {"name": "Canola Exp 1", "species": {"common_name": "Canola"}},
    "cyl_qc_sets": {"name": "outliers"},
    "cyl_trait_sources": {"name": "canola-cyl-sleap-v1"},
}


def _api_error(message, code="P0001"):
    from postgrest import APIError

    return APIError({"message": message, "code": code, "details": None, "hint": None})


def _patch_authed(monkeypatch):
    monkeypatch.setattr(climod, "_authed_client", lambda profile: object())


# --- row shaping ------------------------------------------------------------


def test_build_dataset_row_full():
    row = ds.build_dataset_row(FULL)
    assert row == [
        "canola-v1",
        "1, 3, 5",
        "Canola",
        "Canola Exp 1",
        "outliers",
        "canola-cyl-sleap-v1",
        "2026-02-14",
    ]


def test_build_dataset_row_tolerates_null_relations():
    row = ds.build_dataset_row(
        {
            "name": "bare",
            "timepoints": None,
            "created_at": None,
            "cyl_experiments": None,
            "cyl_qc_sets": None,
            "cyl_trait_sources": None,
        }
    )
    assert row == ["bare", "", "", "", "", "", ""]


def test_build_dataset_record_is_machine_readable():
    rec = ds.build_dataset_record(FULL)
    # raw timepoints list (not the display string) and the same field set as the columns
    assert rec["timepoints"] == [1, 3, 5]
    assert rec["species"] == "Canola"
    assert set(rec) == {
        "name",
        "timepoints",
        "species",
        "experiment",
        "qc_set",
        "trait_source",
        "created",
    }


def test_fmt_timepoints_variants():
    assert ds._fmt_timepoints(None) == ""
    assert ds._fmt_timepoints([1, 2]) == "1, 2"
    assert ds._fmt_timepoints(7) == "7"


# --- fetch_datasets (query + filter) ----------------------------------------


def test_fetch_datasets_builds_joined_query():
    captured = {}

    class _Q:
        def select(self, sel):
            captured["select"] = sel
            return self

        def execute(self):
            return type("R", (), {"data": [FULL]})()

    class _Client:
        def table(self, name):
            captured["table"] = name
            return _Q()

    out = ds.fetch_datasets(_Client())
    assert out == [FULL]
    assert captured["table"] == "cyl_datasets"
    assert "cyl_experiments(*, species(*))" in captured["select"]
    assert "cyl_qc_sets(*)" in captured["select"]
    assert "cyl_trait_sources(*)" in captured["select"]


def test_fetch_datasets_filters_by_experiment():
    captured = {}

    class _Q:
        def select(self, sel):
            return self

        def eq(self, col, val):
            captured["eq"] = (col, val)
            return self

        def execute(self):
            return type("R", (), {"data": []})()

    class _Client:
        def table(self, name):
            return _Q()

    ds.fetch_datasets(_Client(), experiment_id=7)
    assert captured["eq"] == ("experiment_id", 7)


def test_fetch_datasets_empty_returns_list():
    class _Client:
        def table(self, name):
            class _Q:
                def select(self, *a):
                    return self

                def execute(self):
                    return type("R", (), {"data": None})()

            return _Q()

    assert ds.fetch_datasets(_Client()) == []


# --- list command -----------------------------------------------------------


def test_list_renders_rows(monkeypatch):
    _patch_authed(monkeypatch)
    monkeypatch.setattr(ds, "fetch_datasets", lambda client, experiment_id=None: [FULL])
    res = CliRunner().invoke(cli, ["cyl", "datasets", "list"])
    assert res.exit_code == 0, res.output
    assert "canola-v1" in res.output


def test_list_empty_message(monkeypatch):
    _patch_authed(monkeypatch)
    monkeypatch.setattr(ds, "fetch_datasets", lambda client, experiment_id=None: [])
    res = CliRunner().invoke(cli, ["cyl", "datasets", "list"])
    assert res.exit_code == 0, res.output
    assert "No datasets found" in res.output


def test_list_passes_experiment_filter(monkeypatch):
    _patch_authed(monkeypatch)
    captured = {}

    def fake_fetch(client, experiment_id=None):
        captured["experiment_id"] = experiment_id
        return []

    monkeypatch.setattr(ds, "fetch_datasets", fake_fetch)
    res = CliRunner().invoke(cli, ["cyl", "datasets", "list", "--experiment-id", "3"])
    assert res.exit_code == 0, res.output
    assert captured["experiment_id"] == 3


def test_fetch_experiments_with_datasets_joins_names_and_sorts():
    # two-step: experiment ids from cyl_datasets, then labels from cyl_experiments.
    ds_rows = [{"experiment_id": 7}, {"experiment_id": 3}, {"experiment_id": 7}]
    exp_rows = [
        {"id": 7, "name": "Salt Screen", "species": {"common_name": "Rice"}},
        {"id": 3, "name": "Drought", "species": {"common_name": "Arabidopsis"}},
    ]
    captured = {}

    class _Q:
        def __init__(self, table):
            self.table = table

        def select(self, sel):
            captured.setdefault("selects", {})[self.table] = sel
            return self

        def in_(self, col, vals):
            captured["in"] = (col, vals)
            return self

        def is_(self, col, val):
            captured["is_"] = (col, val)
            return self

        def execute(self):
            data = ds_rows if self.table == "cyl_datasets" else exp_rows
            return type("R", (), {"data": data})()

    class _Client:
        def table(self, name):
            return _Q(name)

    out = ds.fetch_experiments_with_datasets(_Client())
    assert captured["in"] == ("id", [3, 7])  # distinct ids, sorted
    assert captured["is_"] == ("deleted_at", "null")  # soft-deleted excluded
    # pin table + select shape — a dropped species join or wrong table would blank the menu label
    assert captured["selects"]["cyl_datasets"] == "experiment_id"
    assert captured["selects"]["cyl_experiments"] == "id, name, species(common_name)"
    assert out == [(3, "Drought (Arabidopsis)"), (7, "Salt Screen (Rice)")]


def test_fetch_experiments_with_datasets_id_breaks_label_tie():
    # two experiments with an identical "name (species)" label -> id decides order (stable menu).
    ds_rows = [{"experiment_id": 30}, {"experiment_id": 10}]
    exp_rows = [
        {"id": 30, "name": "Trial", "species": {"common_name": "Rice"}},
        {"id": 10, "name": "Trial", "species": {"common_name": "Rice"}},
    ]

    class _Q:
        def __init__(self, table):
            self.table = table

        def select(self, sel):
            return self

        def in_(self, col, vals):
            return self

        def is_(self, col, val):
            return self

        def execute(self):
            data = ds_rows if self.table == "cyl_datasets" else exp_rows
            return type("R", (), {"data": data})()

    class _Client:
        def table(self, name):
            return _Q(name)

    out = ds.fetch_experiments_with_datasets(_Client())
    assert out == [(10, "Trial (Rice)"), (30, "Trial (Rice)")]  # same label -> lower id first


def test_list_experiment_menu_filters(monkeypatch):
    _patch_authed(monkeypatch)
    monkeypatch.setattr(
        ds,
        "fetch_experiments_with_datasets",
        lambda client: [(3, "Drought (Arabidopsis)"), (7, "Salt Screen (Rice)")],
    )
    captured = {}

    def fake_fetch(client, experiment_id=None):
        captured["experiment_id"] = experiment_id
        return []

    monkeypatch.setattr(ds, "fetch_datasets", fake_fetch)
    # menu 0) All 1) Drought(3) 2) Salt Screen(7) → pick 2 → experiment 7
    res = CliRunner().invoke(cli, ["cyl", "datasets", "list", "--experiment"], input="2\n")
    assert res.exit_code == 0, res.output
    assert captured["experiment_id"] == 7


def test_list_experiment_non_tty_aborts(monkeypatch):
    # --experiment in a pipe/CI (no stdin) must abort, never list a guessed filter.
    _patch_authed(monkeypatch)
    monkeypatch.setattr(
        ds, "fetch_experiments_with_datasets", lambda client: [(3, "Drought (Arabidopsis)")]
    )
    called = {"fetched": False}

    def fake_fetch(client, experiment_id=None):
        called["fetched"] = True
        return []

    monkeypatch.setattr(ds, "fetch_datasets", fake_fetch)
    res = CliRunner().invoke(cli, ["cyl", "datasets", "list", "--experiment"], input="")
    assert res.exit_code != 0  # aborted, not a guessed listing
    assert called["fetched"] is False


def test_list_experiment_id_bypasses_menu(monkeypatch):
    # The scriptable path: a typed --experiment-id must reach fetch_datasets WITHOUT the menu.
    _patch_authed(monkeypatch)
    called = {"menu": False}

    def _menu(client):
        called["menu"] = True
        return [(3, "Drought (Arabidopsis)")]

    monkeypatch.setattr(ds, "fetch_experiments_with_datasets", _menu)
    captured = {}

    def fake_fetch(client, experiment_id=None):
        captured["experiment_id"] = experiment_id
        return []

    monkeypatch.setattr(ds, "fetch_datasets", fake_fetch)
    # no stdin: if the menu wrongly opened, this would abort instead of exit 0
    res = CliRunner().invoke(cli, ["cyl", "datasets", "list", "--experiment-id", "5"])
    assert res.exit_code == 0, res.output
    assert captured["experiment_id"] == 5  # typed id reaches the fetch
    assert called["menu"] is False  # menu fetcher never called


def test_list_experiment_menu_all_is_no_filter(monkeypatch):
    _patch_authed(monkeypatch)
    monkeypatch.setattr(
        ds, "fetch_experiments_with_datasets", lambda client: [(3, "Drought (Arabidopsis)")]
    )
    captured = {}

    def fake_fetch(client, experiment_id=None):
        captured["experiment_id"] = experiment_id
        return []

    monkeypatch.setattr(ds, "fetch_datasets", fake_fetch)
    res = CliRunner().invoke(cli, ["cyl", "datasets", "list", "--experiment"], input="0\n")
    assert res.exit_code == 0, res.output
    assert captured["experiment_id"] is None  # 0 = All experiments → no filter


def test_list_experiment_and_id_conflict_rejected(monkeypatch):
    _patch_authed(monkeypatch)
    res = CliRunner().invoke(
        cli, ["cyl", "datasets", "list", "--experiment", "--experiment-id", "3"]
    )
    assert res.exit_code != 0
    assert "not both" in res.output.lower()


def test_list_experiment_menu_none_available(monkeypatch):
    _patch_authed(monkeypatch)
    monkeypatch.setattr(ds, "fetch_experiments_with_datasets", lambda client: [])
    res = CliRunner().invoke(cli, ["cyl", "datasets", "list", "--experiment"], input="0\n")
    assert res.exit_code != 0
    assert "No experiments with datasets" in res.output


def test_list_experiment_menu_stderr_clean_stdout_json(monkeypatch):
    _patch_authed(monkeypatch)
    monkeypatch.setattr(
        ds, "fetch_experiments_with_datasets", lambda client: [(3, "Drought (Arabidopsis)")]
    )
    monkeypatch.setattr(ds, "fetch_datasets", lambda client, experiment_id=None: [FULL])
    res = CliRunner().invoke(
        cli, ["cyl", "datasets", "list", "--experiment", "--output", "json"], input="1\n"
    )
    assert res.exit_code == 0, res.output
    json.loads(res.stdout)  # stdout is valid JSON — raises if the menu leaked in
    assert "Select an experiment" not in res.stdout
    assert "Select an experiment" in res.stderr


def test_list_json_output(monkeypatch):
    _patch_authed(monkeypatch)
    monkeypatch.setattr(ds, "fetch_datasets", lambda client, experiment_id=None: [FULL])
    res = CliRunner().invoke(cli, ["cyl", "datasets", "list", "--json"])
    assert res.exit_code == 0, res.output
    payload = json.loads(res.output)
    assert isinstance(payload, list) and payload[0]["name"] == "canola-v1"
    assert payload[0]["timepoints"] == [1, 3, 5]


def test_list_output_csv(monkeypatch):
    _patch_authed(monkeypatch)
    monkeypatch.setattr(ds, "fetch_datasets", lambda client, experiment_id=None: [FULL])
    res = CliRunner().invoke(cli, ["cyl", "datasets", "list", "--output", "csv"])
    assert res.exit_code == 0, res.output
    lines = res.output.strip().splitlines()
    assert lines[0] == "name,timepoints,species,experiment,qc_set,trait_source,created"
    assert lines[1].startswith("canola-v1,")
    # timepoints is a list; in CSV it renders as its repr in one quoted cell (deliberate —
    # JSON keeps the real array). Locked so it can't change silently.
    assert '"[1, 3, 5]"' in lines[1]


def test_list_json_and_conflicting_output_rejected(monkeypatch):
    _patch_authed(monkeypatch)
    monkeypatch.setattr(ds, "fetch_datasets", lambda client, experiment_id=None: [FULL])
    res = CliRunner().invoke(cli, ["cyl", "datasets", "list", "--json", "--output", "csv"])
    assert res.exit_code != 0
    assert "not both" in res.output.lower()


def test_list_json_empty_is_empty_array(monkeypatch):
    _patch_authed(monkeypatch)
    monkeypatch.setattr(ds, "fetch_datasets", lambda client, experiment_id=None: [])
    res = CliRunner().invoke(cli, ["cyl", "datasets", "list", "--json"])
    assert res.exit_code == 0, res.output
    assert json.loads(res.output) == []


# --- create command ---------------------------------------------------------


def _patch_create_ok(monkeypatch, captured):
    _patch_authed(monkeypatch)
    monkeypatch.setattr(ds, "fetch_experiment", lambda client, eid: {"id": eid})
    monkeypatch.setattr(ds, "resolve_trait_source", lambda client, name: 42)
    monkeypatch.setattr(ds, "create_cyl_dataset", lambda client, params: captured.update(params))


def test_create_calls_rpc_with_legacy_param_shape(monkeypatch):
    captured = {}
    _patch_create_ok(monkeypatch, captured)
    res = CliRunner().invoke(
        cli, ["cyl", "datasets", "create", "canola-v1", "1", "canola-cyl-sleap-v1"]
    )
    assert res.exit_code == 0, res.output
    assert captured == {
        "name": "canola-v1",
        "experiment_id": 1,
        "trait_source_id": 42,
        "qc_set_name": {"name": None},
        "timepoints": None,
    }


def test_create_forwards_qc_set_and_timepoints(monkeypatch):
    captured = {}
    _patch_create_ok(monkeypatch, captured)
    res = CliRunner().invoke(
        cli,
        [
            "cyl",
            "datasets",
            "create",
            "d",
            "1",
            "src",
            "--qc-set-name",
            "outliers",
            "--timepoints",
            "1,3,5",
        ],
    )
    assert res.exit_code == 0, res.output
    assert captured["qc_set_name"] == {"name": "outliers"}
    assert captured["timepoints"] == [1, 3, 5]


def test_create_timepoints_repeatable(monkeypatch):
    captured = {}
    _patch_create_ok(monkeypatch, captured)
    res = CliRunner().invoke(
        cli,
        ["cyl", "datasets", "create", "d", "1", "src", "--timepoints", "1", "--timepoints", "3"],
    )
    assert res.exit_code == 0, res.output
    assert captured["timepoints"] == [1, 3]


def test_create_experiment_not_found_makes_no_rpc_call(monkeypatch):
    _patch_authed(monkeypatch)
    monkeypatch.setattr(ds, "fetch_experiment", lambda client, eid: None)
    monkeypatch.setattr(ds, "resolve_trait_source", lambda client, name: 42)
    monkeypatch.setattr(
        ds,
        "create_cyl_dataset",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("RPC must not be called")),
    )
    res = CliRunner().invoke(cli, ["cyl", "datasets", "create", "d", "999", "src"])
    assert res.exit_code != 0
    assert "999" in res.output and "not found" in res.output.lower()


def test_create_trait_source_not_found_makes_no_rpc_call(monkeypatch):
    _patch_authed(monkeypatch)
    monkeypatch.setattr(ds, "fetch_experiment", lambda client, eid: {"id": eid})
    monkeypatch.setattr(ds, "resolve_trait_source", lambda client, name: None)
    monkeypatch.setattr(
        ds,
        "create_cyl_dataset",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("RPC must not be called")),
    )
    res = CliRunner().invoke(cli, ["cyl", "datasets", "create", "d", "1", "missing-src"])
    assert res.exit_code != 0
    assert "missing-src" in res.output and "not found" in res.output.lower()


def test_create_surfaces_rpc_error(monkeypatch):
    _patch_authed(monkeypatch)
    monkeypatch.setattr(ds, "fetch_experiment", lambda client, eid: {"id": eid})
    monkeypatch.setattr(ds, "resolve_trait_source", lambda client, name: 42)

    def boom(client, params):
        raise _api_error('duplicate key value violates unique constraint "cyl_datasets_name_key"')

    monkeypatch.setattr(ds, "create_cyl_dataset", boom)
    res = CliRunner().invoke(cli, ["cyl", "datasets", "create", "dup", "1", "src"])
    assert res.exit_code != 0
    assert "duplicate key" in res.output


# --- get command ------------------------------------------------------------

GET_ROW = {**FULL, "id": 7}


def test_fetch_dataset_by_name_builds_query():
    captured = {}

    class _Q:
        def select(self, sel):
            captured["select"] = sel
            return self

        def eq(self, col, val):
            captured["eq"] = (col, val)
            return self

        def limit(self, n):
            return self

        def execute(self):
            return type("R", (), {"data": [GET_ROW]})()

    class _Client:
        def table(self, name):
            captured["table"] = name
            return _Q()

    out = ds.fetch_dataset_by_name(_Client(), "canola-v1")
    assert out == GET_ROW
    assert captured["table"] == "cyl_datasets"
    assert captured["eq"] == ("name", "canola-v1")
    # trait embed is NOT used (no FK) — traits are fetched separately
    assert "cyl_dataset_traits" not in captured["select"]


def test_fetch_dataset_by_name_missing_returns_none():
    class _Client:
        def table(self, name):
            class _Q:
                def select(self, *a):
                    return self

                def eq(self, *a):
                    return self

                def limit(self, n):
                    return self

                def execute(self):
                    return type("R", (), {"data": []})()

            return _Q()

    assert ds.fetch_dataset_by_name(_Client(), "nope") is None


def test_fetch_dataset_traits_queries_view():
    # one query against the cyl_dataset_trait_names view, filtered by dataset_id;
    # result is deduped + sorted client-side too
    captured = {}

    class _Q:
        def select(self, sel):
            captured["select"] = sel
            return self

        def eq(self, col, val):
            captured["eq"] = (col, val)
            return self

        def execute(self):
            return type(
                "R",
                (),
                {
                    "data": [
                        {"trait_name": "root_width_max"},
                        {"trait_name": "root_depth"},
                        {"trait_name": "root_depth"},
                    ]
                },
            )()

    class _Client:
        def table(self, name):
            captured["table"] = name
            return _Q()

    out = ds.fetch_dataset_traits(_Client(), 7)
    assert out == ["root_depth", "root_width_max"]
    assert captured["table"] == "cyl_dataset_trait_names"
    assert captured["select"] == "trait_name"
    assert captured["eq"] == ("dataset_id", 7)


def test_get_shows_metadata_and_traits(monkeypatch):
    _patch_authed(monkeypatch)
    monkeypatch.setattr(ds, "fetch_dataset_by_name", lambda client, name: GET_ROW)
    monkeypatch.setattr(
        ds, "fetch_dataset_traits", lambda client, did: ["root_depth", "root_width_max"]
    )
    res = CliRunner().invoke(cli, ["cyl", "datasets", "get", "canola-v1"])
    assert res.exit_code == 0, res.output
    assert "Dataset" in res.output  # table title (cell values may wrap across lines)
    assert "2 unique" in res.output
    assert "root_depth" in res.output and "root_width_max" in res.output


def test_get_not_found(monkeypatch):
    _patch_authed(monkeypatch)
    monkeypatch.setattr(ds, "fetch_dataset_by_name", lambda client, name: None)
    res = CliRunner().invoke(cli, ["cyl", "datasets", "get", "missing"])
    assert res.exit_code != 0
    assert "missing" in res.output and "not found" in res.output.lower()


def test_get_json(monkeypatch):
    _patch_authed(monkeypatch)
    monkeypatch.setattr(ds, "fetch_dataset_by_name", lambda client, name: GET_ROW)
    monkeypatch.setattr(
        ds, "fetch_dataset_traits", lambda client, did: ["root_depth", "root_width_max"]
    )
    res = CliRunner().invoke(cli, ["cyl", "datasets", "get", "canola-v1", "--json"])
    assert res.exit_code == 0, res.output
    payload = json.loads(res.output)
    assert payload["name"] == "canola-v1"
    assert payload["traits"] == ["root_depth", "root_width_max"]


# --- grouping ---------------------------------------------------------------


def test_datasets_grouped_under_cyl_not_top_level():
    root = CliRunner().invoke(cli, ["--help"])
    assert "datasets" not in root.output  # not a top-level command
    sub = CliRunner().invoke(cli, ["cyl", "datasets", "--help"])
    assert "list" in sub.output
    assert "create" in sub.output
    assert "get" in sub.output
