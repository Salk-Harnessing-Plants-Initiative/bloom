"""bloomctl plate experiments — list (row shaping, sort, filters, json; mocked client)."""

import json

from click.testing import CliRunner

import bloomctl.cli as climod
import bloomctl.plate.experiments as ex
from bloomctl._postgrest import PAGE_SIZE
from bloomctl.cli import cli

# Out of order to prove the (species, name, rig, id) sort; "Tilt" exists on two rigs, and
# rig-a has the higher id so a sort that ignores the rig gets the order wrong.
EXPS = [
    {"id": 2, "name": "Tilt", "system_name": "rig-b", "species": {"common_name": "Rice"}},
    {"id": 5, "name": "Tilt", "system_name": "rig-b", "species": {"common_name": "Arabidopsis"}},
    {"id": 6, "name": "Tilt", "system_name": "rig-a", "species": {"common_name": "Arabidopsis"}},
    {"id": 1, "name": "Gravity", "system_name": None, "species": {"common_name": "Arabidopsis"}},
]


def _patch_authed(monkeypatch):
    monkeypatch.setattr(climod, "_authed_client", lambda profile: object())


class _Query:
    """Records every builder call; `execute` returns `data`."""

    def __init__(self, data, calls):
        self._data = data
        self.calls = calls

    def __getattr__(self, method):
        def _call(*args, **kwargs):
            self.calls.append((method, args))
            return self

        return _call

    def execute(self):
        return type("R", (), {"data": self._data})()


class _Client:
    def __init__(self, data):
        self.data = data
        self.tables = []
        self.calls = []

    def table(self, name):
        self.tables.append(name)
        return _Query(self.data, self.calls)


# --- row shaping ------------------------------------------------------------


def test_build_experiment_row():
    row = ex.build_experiment_row(EXPS[0])
    assert row == ["Rice", "Tilt", "rig-b", "2"]


def test_build_experiment_row_tolerates_missing_species_and_rig():
    row = ex.build_experiment_row({"id": 9, "name": "Loose", "system_name": None, "species": None})
    assert row == ["", "Loose", "", "9"]


def test_build_experiment_record():
    assert ex.build_experiment_record(EXPS[0]) == {
        "species": "Rice",
        "experiment": "Tilt",
        "rig": "rig-b",
        "experiment_id": 2,
    }


def test_sort_orders_species_name_rig_then_id():
    ordered = sorted(EXPS, key=ex.experiment_sort_key)
    # Arabidopsis/Gravity, Arabidopsis/Tilt rig-a, Arabidopsis/Tilt rig-b, Rice/Tilt
    assert [e["id"] for e in ordered] == [1, 6, 5, 2]


def test_sort_breaks_full_ties_by_id():
    # A NULL rig is not unique, so species, name and rig can all match; id decides.
    tied = [
        {"id": 9, "name": "Tilt", "system_name": None, "species": {"common_name": "Rice"}},
        {"id": 3, "name": "Tilt", "system_name": None, "species": {"common_name": "Rice"}},
    ]
    assert [e["id"] for e in sorted(tied, key=ex.experiment_sort_key)] == [3, 9]


# --- query ------------------------------------------------------------------


def test_fetch_experiments_builds_bounded_query():
    client = _Client(EXPS)
    assert ex.fetch_experiments(client) == EXPS
    assert client.tables == ["gravi_experiments"]
    assert ("select", ("*, species(*)",)) in client.calls
    assert ("order", ("id",)) in client.calls
    assert ("limit", (ex.DEFAULT_LIMIT,)) in client.calls  # never an unbounded query
    assert not [c for c in client.calls if c[0] == "eq"]  # no species filter by default


def test_fetch_experiments_filters_by_species_id():
    client = _Client(EXPS)
    ex.fetch_experiments(client, species_id=7, limit=10)
    assert ("eq", ("species_id", 7)) in client.calls
    assert ("limit", (10,)) in client.calls


def test_fetch_experiments_empty():
    assert ex.fetch_experiments(_Client(None)) == []


def test_fetch_species_with_experiments_dedups_and_sorts():
    rows = [
        {"species_id": 2, "species": {"common_name": "Rice"}},
        {"species_id": 1, "species": {"common_name": "Arabidopsis"}},
        {"species_id": 2, "species": {"common_name": "Rice"}},
        {"species_id": None, "species": None},
    ]
    client = _Client(rows)
    assert ex.fetch_species_with_experiments(client) == [(1, "Arabidopsis"), (2, "Rice")]
    assert client.tables == ["gravi_experiments"]  # only species that have plate experiments
    assert ("select", ("species_id, species(common_name)",)) in client.calls
    assert ("order", ("id",)) in client.calls  # stable order, so pages neither skip nor repeat
    assert ("range", (0, PAGE_SIZE - 1)) in client.calls


def test_fetch_species_reads_past_the_first_page():
    # A species whose only experiment is past the first page must still be found.
    first = [{"species_id": 1, "species": {"common_name": "Arabidopsis"}}] * PAGE_SIZE
    second = [{"species_id": 2, "species": {"common_name": "Rice"}}]
    pages = iter([first, second])

    class _Paged(_Client):
        def table(self, name):
            return _Query(next(pages), self.calls)

    client = _Paged(None)
    assert ex.fetch_species_with_experiments(client) == [(1, "Arabidopsis"), (2, "Rice")]
    assert ("range", (PAGE_SIZE, 2 * PAGE_SIZE - 1)) in client.calls


def test_fetch_experiments_has_no_soft_delete_filter():
    # gravi_experiments has no deleted_at column; filtering on it fails on the server.
    client = _Client(EXPS)
    ex.fetch_experiments(client)
    assert not [c for c in client.calls if c[0] == "is_"]


# --- command ----------------------------------------------------------------


def test_list_renders_table(monkeypatch):
    _patch_authed(monkeypatch)
    monkeypatch.setattr(ex, "fetch_experiments", lambda client, **kw: EXPS)
    res = CliRunner().invoke(cli, ["plate", "experiments", "list"])
    assert res.exit_code == 0, res.output
    assert "Plate experiments" in res.output
    for token in ("Arabidopsis", "Rice", "Gravity", "Tilt", "rig-a", "rig-b"):
        assert token in res.output, f"{token!r} missing from table output"


def test_list_json_sorted(monkeypatch):
    _patch_authed(monkeypatch)
    monkeypatch.setattr(ex, "fetch_experiments", lambda client, **kw: EXPS)
    res = CliRunner().invoke(cli, ["plate", "experiments", "list", "--json"])
    assert res.exit_code == 0, res.output
    payload = json.loads(res.output)
    assert [e["experiment_id"] for e in payload] == [1, 6, 5, 2]
    assert payload[1] == {
        "species": "Arabidopsis",
        "experiment": "Tilt",
        "rig": "rig-a",
        "experiment_id": 6,
    }


def test_list_csv_has_header(monkeypatch):
    _patch_authed(monkeypatch)
    monkeypatch.setattr(ex, "fetch_experiments", lambda client, **kw: EXPS)
    res = CliRunner().invoke(cli, ["plate", "experiments", "list", "--output", "csv"])
    assert res.exit_code == 0, res.output
    assert res.output.splitlines()[0] == "species,experiment,rig,experiment_id"


def test_list_empty(monkeypatch):
    _patch_authed(monkeypatch)
    monkeypatch.setattr(ex, "fetch_experiments", lambda client, **kw: [])
    res = CliRunner().invoke(cli, ["plate", "experiments", "list"])
    assert res.exit_code == 0
    assert "No plate experiments found" in res.output


def test_list_surfaces_api_error(monkeypatch):
    from postgrest import APIError

    _patch_authed(monkeypatch)

    def _boom(client, **kw):
        raise APIError({"message": "permission denied", "code": "42501"})

    monkeypatch.setattr(ex, "fetch_experiments", _boom)
    res = CliRunner().invoke(cli, ["plate", "experiments", "list"])
    assert res.exit_code != 0
    assert "permission denied" in res.output


def test_list_warns_when_limit_is_hit(monkeypatch):
    _patch_authed(monkeypatch)
    monkeypatch.setattr(ex, "fetch_experiments", lambda client, **kw: EXPS[:2])
    res = CliRunner().invoke(cli, ["plate", "experiments", "list", "--limit", "2", "--json"])
    assert res.exit_code == 0, res.output
    assert "capped at --limit 2" in res.stderr
    assert len(json.loads(res.stdout)) == 2  # warning stays off stdout


def test_list_limit_passed_through_and_capped(monkeypatch):
    _patch_authed(monkeypatch)
    captured = {}

    def _fetch(client, *, species_id=None, limit=ex.DEFAULT_LIMIT):
        captured["limit"] = limit
        return EXPS

    monkeypatch.setattr(ex, "fetch_experiments", _fetch)
    ok = CliRunner().invoke(cli, ["plate", "experiments", "list", "--limit", "5"])
    assert ok.exit_code == 0, ok.output
    assert captured["limit"] == 5

    over = CliRunner().invoke(
        cli, ["plate", "experiments", "list", "--limit", str(ex.DEFAULT_LIMIT + 1)]
    )
    assert over.exit_code != 0
    assert "range" in over.output.lower()


def test_list_species_value_resolves_and_filters(monkeypatch):
    _patch_authed(monkeypatch)
    monkeypatch.setattr(
        ex, "fetch_species_with_experiments", lambda client: [(1, "Arabidopsis"), (2, "Rice")]
    )
    captured = {}

    def _fetch(client, *, species_id=None, limit=ex.DEFAULT_LIMIT):
        captured["species_id"] = species_id
        return [EXPS[0]]

    monkeypatch.setattr(ex, "fetch_experiments", _fetch)
    res = CliRunner().invoke(cli, ["plate", "experiments", "list", "--species", "rice"])
    assert res.exit_code == 0, res.output
    assert captured["species_id"] == 2


def test_list_species_value_unknown_name_errors(monkeypatch):
    _patch_authed(monkeypatch)
    monkeypatch.setattr(ex, "fetch_species_with_experiments", lambda client: [(2, "Rice")])
    monkeypatch.setattr(
        ex,
        "fetch_experiments",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not fetch on a bad name")),
    )
    res = CliRunner().invoke(cli, ["plate", "experiments", "list", "--species", "Sorghum"])
    assert res.exit_code != 0
    assert "No species named 'Sorghum' with plate experiments" in res.output


def test_list_species_value_and_menu_conflict(monkeypatch):
    _patch_authed(monkeypatch)
    res = CliRunner().invoke(
        cli, ["plate", "experiments", "list", "--species", "Rice", "--species-menu"]
    )
    assert res.exit_code != 0
    assert "not both" in res.output.lower()


def test_list_species_menu_filters(monkeypatch):
    _patch_authed(monkeypatch)
    monkeypatch.setattr(
        ex, "fetch_species_with_experiments", lambda client: [(1, "Arabidopsis"), (2, "Rice")]
    )
    captured = {}

    def _fetch(client, *, species_id=None, limit=ex.DEFAULT_LIMIT):
        captured["species_id"] = species_id
        return [EXPS[0]]

    monkeypatch.setattr(ex, "fetch_experiments", _fetch)
    # menu: 0) All  1) Arabidopsis  2) Rice
    res = CliRunner().invoke(
        cli, ["plate", "experiments", "list", "--species-menu", "--json"], input="2\n"
    )
    assert res.exit_code == 0, res.output
    assert captured["species_id"] == 2


def test_list_species_menu_none_available(monkeypatch):
    _patch_authed(monkeypatch)
    monkeypatch.setattr(ex, "fetch_species_with_experiments", lambda client: [])
    res = CliRunner().invoke(cli, ["plate", "experiments", "list", "--species-menu"], input="0\n")
    assert res.exit_code != 0
    assert "No species with plate experiments" in res.output
