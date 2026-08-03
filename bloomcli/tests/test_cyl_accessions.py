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
        {"accession_id": 4, "species_name": "Canola", "accession_name": "Bay-0", "plant_count": 5}
    )
    assert rec == {
        "accession_id": 4,
        "species": "Canola",
        "accession": "Bay-0",
        "plant_count": 5,
    }


def test_sample_count_sort_key_species_then_name():
    ordered = sorted(COUNTS, key=acc.sample_count_sort_key)
    assert [(r["species_name"], r["accession_name"]) for r in ordered] == [
        ("Canola", "Ames"),
        ("Canola", "Bay-0"),
        ("Rice", "IR64"),
    ]


def test_accession_sort_key_orders_by_name():
    unsorted = [
        {"accession_id": 9, "accession_name": "Col-0"},
        {"accession_id": 4, "accession_name": "Bay-0"},
    ]
    ordered = sorted(unsorted, key=acc.accession_sort_key)
    assert [r["accession_name"] for r in ordered] == ["Bay-0", "Col-0"]


def test_accession_sort_key_breaks_name_ties_by_id():
    # same name, different id → id decides order (deterministic run-to-run).
    tied = [
        {"accession_id": 20, "accession_name": "Col-0"},
        {"accession_id": 7, "accession_name": "Col-0"},
    ]
    ordered = sorted(tied, key=acc.accession_sort_key)
    assert [r["accession_id"] for r in ordered] == [7, 20]


def test_sample_count_sort_key_breaks_ties_by_id():
    # same species AND name, different id → id decides order.
    tied = [
        {"species_name": "Rice", "accession_id": 30, "accession_name": "IR64", "plant_count": 1},
        {"species_name": "Rice", "accession_id": 3, "accession_name": "IR64", "plant_count": 1},
    ]
    ordered = sorted(tied, key=acc.sample_count_sort_key)
    assert [r["accession_id"] for r in ordered] == [3, 30]


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
    # a dropped column would silently blank the output — assert the select carries them.
    for col in ("species_name", "accession_id", "accession_name", "plant_count"):
        assert col in captured["select"], f"{col!r} missing from select"


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


# --- menu fetchers ----------------------------------------------------------


def test_fetch_species_with_accessions_dedups_sorts_drops_null():
    rows = [
        {"species_name": "Rice"},
        {"species_name": "Canola"},
        {"species_name": "Rice"},  # duplicate
        {"species_name": None},  # dropped
    ]

    captured = {}

    class _Q:
        def select(self, sel):
            captured["select"] = sel
            return self

        def execute(self):
            return type("R", (), {"data": rows})()

    class _Client:
        def table(self, name):
            captured["table"] = name
            return _Q()

    assert acc.fetch_species_with_accessions(_Client()) == ["Canola", "Rice"]
    assert captured["table"] == "cyl_accession_sample_counts"  # the species-bearing view
    assert captured["select"] == "species_name"  # a wrong column would silently blank the menu


def test_fetch_experiments_with_accessions_joins_names_and_sorts():
    # two-step: ids from the accessions view, then labels from cyl_experiments.
    acc_rows = [{"experiment_id": 7}, {"experiment_id": 3}, {"experiment_id": 7}]
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
            data = acc_rows if self.table == "cyl_experiment_accessions" else exp_rows
            return type("R", (), {"data": data})()

    class _Client:
        def table(self, name):
            return _Q(name)

    out = acc.fetch_experiments_with_accessions(_Client())
    assert captured["in"] == ("id", [3, 7])  # distinct ids, sorted, passed to cyl_experiments
    assert captured["is_"] == ("deleted_at", "null")  # soft-deleted experiments excluded
    # the label needs id + name + the joined species — a dropped join would blank the menu label
    assert captured["selects"]["cyl_experiment_accessions"] == "experiment_id"
    assert captured["selects"]["cyl_experiments"] == "id, name, species(common_name)"
    # labeled "name (species)" and sorted by label (Drought before Salt Screen)
    assert out == [(3, "Drought (Arabidopsis)"), (7, "Salt Screen (Rice)")]


def test_fetch_experiments_with_accessions_id_breaks_label_tie(monkeypatch):
    # two experiments with an identical "name (species)" label -> id decides order (stable menu).
    acc_rows = [{"experiment_id": 30}, {"experiment_id": 10}]
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
            data = acc_rows if self.table == "cyl_experiment_accessions" else exp_rows
            return type("R", (), {"data": data})()

    class _Client:
        def table(self, name):
            return _Q(name)

    out = acc.fetch_experiments_with_accessions(_Client())
    assert out == [(10, "Trial (Rice)"), (30, "Trial (Rice)")]  # same label -> lower id first


# --- commands ---------------------------------------------------------------


def test_list_experiment_id_bypasses_menu(monkeypatch):
    # The scriptable path: a typed --experiment-id must reach the fetch WITHOUT ever
    # opening the menu (a regression here would hang/break any pipeline).
    _patch_authed(monkeypatch)
    called = {"menu": False}

    def _menu_fetch(client):
        called["menu"] = True
        return [(3, "Drought (Arabidopsis)")]

    monkeypatch.setattr(acc, "fetch_experiments_with_accessions", _menu_fetch)
    captured = {}

    def _fetch(client, eid):
        captured["eid"] = eid
        return EXP_ACC

    monkeypatch.setattr(acc, "fetch_experiment_accessions", _fetch)
    # no stdin: if the menu wrongly opened, this would abort instead of exit 0
    res = CliRunner().invoke(cli, ["cyl", "accessions", "list", "--experiment-id", "7"])
    assert res.exit_code == 0, res.output
    assert captured["eid"] == 7  # typed id reaches the fetch unchanged
    assert called["menu"] is False  # menu fetcher never called


def test_list_no_id_opens_experiment_menu(monkeypatch):
    _patch_authed(monkeypatch)
    monkeypatch.setattr(
        acc,
        "fetch_experiments_with_accessions",
        lambda client: [(3, "Drought (Arabidopsis)"), (7, "Salt Screen (Rice)")],
    )
    captured = {}

    def _fetch(client, eid):
        captured["eid"] = eid
        return EXP_ACC

    monkeypatch.setattr(acc, "fetch_experiment_accessions", _fetch)
    # menu 1) Drought(3) 2) Salt Screen(7) → pick 2 → experiment 7
    res = CliRunner().invoke(cli, ["cyl", "accessions", "list"], input="2\n")
    assert res.exit_code == 0, res.output
    assert captured["eid"] == 7  # picked experiment id reaches the fetch


def test_list_no_id_non_tty_aborts(monkeypatch):
    _patch_authed(monkeypatch)
    monkeypatch.setattr(
        acc, "fetch_experiments_with_accessions", lambda client: [(3, "Drought (Arabidopsis)")]
    )
    called = {"fetched": False}

    def _fetch(client, eid):
        called["fetched"] = True
        return EXP_ACC

    monkeypatch.setattr(acc, "fetch_experiment_accessions", _fetch)
    res = CliRunner().invoke(cli, ["cyl", "accessions", "list"], input="")
    assert res.exit_code != 0  # no input → abort, never a guessed experiment
    assert called["fetched"] is False


def test_list_no_experiments_with_accessions(monkeypatch):
    _patch_authed(monkeypatch)
    monkeypatch.setattr(acc, "fetch_experiments_with_accessions", lambda client: [])
    res = CliRunner().invoke(cli, ["cyl", "accessions", "list"], input="1\n")
    assert res.exit_code != 0
    assert "No experiments with accessions" in res.output


def test_list_json_sorted(monkeypatch):
    _patch_authed(monkeypatch)
    monkeypatch.setattr(acc, "fetch_experiment_accessions", lambda client, eid: EXP_ACC)
    res = CliRunner().invoke(cli, ["cyl", "accessions", "list", "--experiment-id", "7", "--json"])
    assert res.exit_code == 0, res.output
    payload = json.loads(res.output)
    assert [r["accession_name"] for r in payload] == ["Bay-0", "Col-0"]  # sorted by name


def test_list_output_csv(monkeypatch):
    _patch_authed(monkeypatch)
    monkeypatch.setattr(acc, "fetch_experiment_accessions", lambda client, eid: EXP_ACC)
    res = CliRunner().invoke(
        cli, ["cyl", "accessions", "list", "--experiment-id", "7", "--output", "csv"]
    )
    assert res.exit_code == 0, res.output
    lines = res.output.strip().splitlines()
    assert lines[0] == "accession_id,accession_name"
    assert lines[1] == "4,Bay-0"  # sorted first; accession_id value pinned (the join key)


def test_list_json_and_conflicting_output_rejected(monkeypatch):
    _patch_authed(monkeypatch)
    monkeypatch.setattr(acc, "fetch_experiment_accessions", lambda client, eid: EXP_ACC)
    res = CliRunner().invoke(
        cli,
        ["cyl", "accessions", "list", "--experiment-id", "7", "--json", "--output", "csv"],
    )
    assert res.exit_code != 0
    assert "not both" in res.output.lower()


def test_list_renders_table(monkeypatch):
    _patch_authed(monkeypatch)
    monkeypatch.setattr(acc, "fetch_experiment_accessions", lambda client, eid: EXP_ACC)
    res = CliRunner().invoke(cli, ["cyl", "accessions", "list", "--experiment-id", "7"])
    assert res.exit_code == 0, res.output
    assert "Accessions" in res.output  # table title
    # names and ids both rendered — fails if a column is dropped/swapped
    for token in ("Bay-0", "Col-0", "4", "9"):
        assert token in res.output, f"{token!r} missing from table output"


def test_list_empty(monkeypatch):
    _patch_authed(monkeypatch)
    monkeypatch.setattr(acc, "fetch_experiment_accessions", lambda client, eid: [])
    res = CliRunner().invoke(cli, ["cyl", "accessions", "list", "--experiment-id", "7"])
    assert res.exit_code == 0
    assert "No accessions found" in res.output


def test_list_json_empty_is_empty_array(monkeypatch):
    # --json must always emit parseable JSON, even when empty (not the human message).
    _patch_authed(monkeypatch)
    monkeypatch.setattr(acc, "fetch_experiment_accessions", lambda client, eid: [])
    res = CliRunner().invoke(cli, ["cyl", "accessions", "list", "--experiment-id", "7", "--json"])
    assert res.exit_code == 0, res.output
    assert json.loads(res.output) == []


def test_sample_counts_output_csv(monkeypatch):
    _patch_authed(monkeypatch)
    monkeypatch.setattr(acc, "fetch_accession_sample_counts", lambda client, species=None: COUNTS)
    res = CliRunner().invoke(cli, ["cyl", "accessions", "sample-counts", "--output", "csv"])
    assert res.exit_code == 0, res.output
    lines = res.output.strip().splitlines()
    assert lines[0] == "accession_id,species,accession,plant_count"
    # sorted first (Canola/Ames); pins every cell incl. accession_id + plant_count
    assert lines[1] == "2,Canola,Ames,8"


def test_sample_counts_json_and_conflicting_output_rejected(monkeypatch):
    _patch_authed(monkeypatch)
    monkeypatch.setattr(acc, "fetch_accession_sample_counts", lambda client, species=None: COUNTS)
    res = CliRunner().invoke(
        cli, ["cyl", "accessions", "sample-counts", "--json", "--output", "csv"]
    )
    assert res.exit_code != 0
    assert "not both" in res.output.lower()


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


def test_sample_counts_species_value_is_scriptable(monkeypatch):
    # The 0.1.0a2 scriptable path: --species NAME filters directly, no menu, no stdin.
    _patch_authed(monkeypatch)
    called = {"menu": False}
    monkeypatch.setattr(
        acc,
        "fetch_species_with_accessions",
        lambda client: called.__setitem__("menu", True) or ["Canola"],
    )
    captured = {}

    def _fetch(client, species=None):
        captured["species"] = species
        return []

    monkeypatch.setattr(acc, "fetch_accession_sample_counts", _fetch)
    # no stdin: if this wrongly opened the menu it would abort instead of exit 0
    res = CliRunner().invoke(cli, ["cyl", "accessions", "sample-counts", "--species", "Canola"])
    assert res.exit_code == 0, res.output
    assert captured["species"] == "Canola"  # typed name reaches the fetch
    assert called["menu"] is False  # the menu fetcher was never called


def test_sample_counts_species_value_and_menu_conflict(monkeypatch):
    _patch_authed(monkeypatch)
    res = CliRunner().invoke(
        cli, ["cyl", "accessions", "sample-counts", "--species", "Canola", "--species-menu"]
    )
    assert res.exit_code != 0
    assert "not both" in res.output.lower()


def test_sample_counts_menu_species_passed_to_fetch(monkeypatch):
    # --species-menu opens a menu; the picked name must reach the fetch.
    _patch_authed(monkeypatch)
    monkeypatch.setattr(acc, "fetch_species_with_accessions", lambda client: ["Canola", "Rice"])
    captured = {}

    def _fetch(client, species=None):
        captured["species"] = species
        return []

    monkeypatch.setattr(acc, "fetch_accession_sample_counts", _fetch)
    # menu 0) All 1) Canola 2) Rice → pick 1 → Canola
    res = CliRunner().invoke(
        cli, ["cyl", "accessions", "sample-counts", "--species-menu"], input="1\n"
    )
    assert res.exit_code == 0, res.output
    assert captured["species"] == "Canola"


def test_sample_counts_menu_all_is_no_filter(monkeypatch):
    _patch_authed(monkeypatch)
    monkeypatch.setattr(acc, "fetch_species_with_accessions", lambda client: ["Canola"])
    captured = {}

    def _fetch(client, species=None):
        captured["species"] = species
        return []

    monkeypatch.setattr(acc, "fetch_accession_sample_counts", _fetch)
    res = CliRunner().invoke(
        cli, ["cyl", "accessions", "sample-counts", "--species-menu"], input="0\n"
    )
    assert res.exit_code == 0, res.output
    assert captured["species"] is None  # 0 = All species → no filter


def test_sample_counts_menu_none_available(monkeypatch):
    _patch_authed(monkeypatch)
    monkeypatch.setattr(acc, "fetch_species_with_accessions", lambda client: [])
    res = CliRunner().invoke(
        cli, ["cyl", "accessions", "sample-counts", "--species-menu"], input="0\n"
    )
    assert res.exit_code != 0
    assert "No species with accessions" in res.output


def test_sample_counts_menu_stderr_clean_stdout_json(monkeypatch):
    # menu on stderr; stdout stays valid JSON under --output json.
    _patch_authed(monkeypatch)
    monkeypatch.setattr(acc, "fetch_species_with_accessions", lambda client: ["Canola"])
    monkeypatch.setattr(acc, "fetch_accession_sample_counts", lambda client, species=None: COUNTS)
    res = CliRunner().invoke(
        cli,
        ["cyl", "accessions", "sample-counts", "--species-menu", "--output", "json"],
        input="1\n",
    )
    assert res.exit_code == 0, res.output
    json.loads(res.stdout)  # stdout is valid JSON — raises if the menu leaked in
    assert "Select a species" not in res.stdout
    assert "Select a species" in res.stderr


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


def test_sample_counts_json_empty_is_empty_array(monkeypatch):
    _patch_authed(monkeypatch)
    monkeypatch.setattr(acc, "fetch_accession_sample_counts", lambda client, species=None: [])
    res = CliRunner().invoke(cli, ["cyl", "accessions", "sample-counts", "--json"])
    assert res.exit_code == 0, res.output
    assert json.loads(res.output) == []


def test_sample_counts_empty_echoes_species_filter(monkeypatch):
    # an empty result after picking a species should name it, so an empty species is
    # distinguishable from an empty database.
    _patch_authed(monkeypatch)
    monkeypatch.setattr(acc, "fetch_species_with_accessions", lambda client: ["Canola"])
    monkeypatch.setattr(acc, "fetch_accession_sample_counts", lambda client, species=None: [])
    res = CliRunner().invoke(
        cli, ["cyl", "accessions", "sample-counts", "--species-menu"], input="1\n"
    )
    assert res.exit_code == 0
    assert "No sample counts found for species 'Canola'." in res.output


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


def test_list_apierror_without_message_falls_back(monkeypatch):
    # APIError.message is None when the body has no "message" key; fall back to str(exc)
    # so the diagnostic (code/details) survives instead of printing "Error: None".
    from postgrest import APIError

    _patch_authed(monkeypatch)

    def _boom(client, experiment_id):
        raise APIError({"code": "42P01", "details": "relation does not exist"})

    monkeypatch.setattr(acc, "fetch_experiment_accessions", _boom)
    res = CliRunner().invoke(cli, ["cyl", "accessions", "list", "--experiment-id", "7"])
    assert res.exit_code != 0
    assert "None" not in res.output  # not "Error: None"
    assert "42P01" in res.output or "relation does not exist" in res.output


def test_sample_counts_apierror_without_message_falls_back(monkeypatch):
    from postgrest import APIError

    _patch_authed(monkeypatch)

    def _boom(client, species=None):
        raise APIError({"code": "42P01", "details": "relation does not exist"})

    monkeypatch.setattr(acc, "fetch_accession_sample_counts", _boom)
    res = CliRunner().invoke(cli, ["cyl", "accessions", "sample-counts"])
    assert res.exit_code != 0
    assert "None" not in res.output
    assert "42P01" in res.output or "relation does not exist" in res.output
