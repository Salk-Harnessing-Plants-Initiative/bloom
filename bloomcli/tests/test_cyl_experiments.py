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
    assert ex.build_experiment_row({"id": 5, "name": "Exp 5", "species": None}) == [
        "",
        "Exp 5",
        "5",
    ]


def test_build_experiment_record():
    rec = ex.build_experiment_record(
        {"id": 7, "name": "Exp 7", "species": {"common_name": "Canola"}}
    )
    assert rec == {"species": "Canola", "experiment": "Exp 7", "experiment_id": 7}


def test_sort_key_orders_species_then_name():
    ordered = sorted(EXPS, key=ex.experiment_sort_key)
    assert [e["id"] for e in ordered] == [3, 1, 2]  # Canola/Alpha, Canola/Beta, Rice/Alpha


def test_sort_key_breaks_ties_by_id():
    # same species AND name → id decides order, so output is deterministic run-to-run.
    tied = [
        {"id": 205, "name": "Wave A", "species": {"common_name": "Rice"}},
        {"id": 101, "name": "Wave A", "species": {"common_name": "Rice"}},
    ]
    assert [e["id"] for e in sorted(tied, key=ex.experiment_sort_key)] == [101, 205]


# --- query ------------------------------------------------------------------


def test_fetch_experiments_builds_query():
    captured = {"is_": None, "order": None}

    class _Q:
        def select(self, sel):
            captured["select"] = sel
            return self

        def is_(self, col, val):
            captured["is_"] = (col, val)
            return self

        def eq(self, col, val):
            captured["eq"] = (col, val)
            return self

        def order(self, col, **kw):
            captured["order"] = col
            return self

        def limit(self, n):
            captured["limit"] = n
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
    assert captured["select"] == "*, species(*)"  # full select: id/name + species
    assert captured["is_"] == ("deleted_at", "null")  # soft-deleted experiments excluded
    assert captured["order"] == "id"  # deterministic base order
    assert captured["limit"] == ex.DEFAULT_LIMIT  # explicit cap, never an unbounded query
    assert captured.get("eq") is None  # no species filter unless species_id is passed


def test_fetch_experiments_filters_by_species_id():
    captured = {}

    class _Q:
        def select(self, sel):
            return self

        def is_(self, *a):
            return self

        def eq(self, col, val):
            captured["eq"] = (col, val)
            return self

        def order(self, *a, **k):
            return self

        def limit(self, n):
            return self

        def execute(self):
            return type("R", (), {"data": EXPS})()

    class _Client:
        def table(self, name):
            return _Q()

    ex.fetch_experiments(_Client(), species_id=7)
    assert captured["eq"] == ("species_id", 7)


def test_fetch_experiments_empty():
    class _Client:
        def table(self, name):
            class _Q:
                def select(self, *a):
                    return self

                def is_(self, *a):
                    return self

                def eq(self, *a):
                    return self

                def order(self, *a, **k):
                    return self

                def limit(self, n):
                    return self

                def execute(self):
                    return type("R", (), {"data": None})()

            return _Q()

    assert ex.fetch_experiments(_Client()) == []


def test_fetch_species_with_experiments_dedups_and_sorts():
    rows = [
        {"species_id": 2, "species": {"common_name": "Rice"}},
        {"species_id": 1, "species": {"common_name": "Canola"}},
        {"species_id": 2, "species": {"common_name": "Rice"}},  # duplicate species
        {"species_id": None, "species": None},  # no species → skipped
    ]
    captured = {}

    class _Q:
        def select(self, sel):
            captured["select"] = sel
            return self

        def is_(self, col, val):
            captured["is_"] = (col, val)
            return self

        def limit(self, n):
            captured["limit"] = n
            return self

        def execute(self):
            return type("R", (), {"data": rows})()

    class _Client:
        def table(self, name):
            captured["table"] = name
            return _Q()

    out = ex.fetch_species_with_experiments(_Client())
    assert out == [(1, "Canola"), (2, "Rice")]  # deduped, sorted by common name
    assert captured["table"] == "cyl_experiments"
    assert captured["is_"] == ("deleted_at", "null")  # only species of live experiments
    assert "species(common_name)" in captured["select"]
    assert captured["limit"] == ex.DEFAULT_LIMIT  # bounded, never an unbounded query


def test_fetch_species_with_experiments_id_breaks_common_name_tie():
    # Two species sharing a common name → species_id decides order, so the menu is stable.
    rows = [
        {"species_id": 30, "species": {"common_name": "Maize"}},
        {"species_id": 10, "species": {"common_name": "Maize"}},
    ]

    class _Q:
        def select(self, sel):
            return self

        def is_(self, *a):
            return self

        def limit(self, n):
            return self

        def execute(self):
            return type("R", (), {"data": rows})()

    class _Client:
        def table(self, name):
            return _Q()

    assert ex.fetch_species_with_experiments(_Client()) == [(10, "Maize"), (30, "Maize")]


def test_select_species_maps_choice_to_id(monkeypatch):
    monkeypatch.setattr("click.prompt", lambda *a, **k: 2)
    # menu: 1) Canola(10)  2) Rice(20) → choosing 2 returns id 20
    assert ex.select_species_interactively([(10, "Canola"), (20, "Rice")]) == 20


def test_select_species_zero_is_all(monkeypatch):
    monkeypatch.setattr("click.prompt", lambda *a, **k: 0)
    assert ex.select_species_interactively([(10, "Canola")]) is None  # 0 = All species


# --- command ----------------------------------------------------------------


def test_list_renders_table(monkeypatch):
    _patch_authed(monkeypatch)
    monkeypatch.setattr(ex, "fetch_experiments", lambda client, **kw: EXPS)
    res = CliRunner().invoke(cli, ["cyl", "experiments", "list"])
    assert res.exit_code == 0, res.output
    assert "Experiments" in res.output  # table title
    # species, names, and ids all rendered — fails if a column is dropped or swapped
    for token in ("Canola", "Rice", "Alpha", "Beta", "1", "2", "3"):
        assert token in res.output, f"{token!r} missing from table output"


def test_list_surfaces_api_error(monkeypatch):
    from postgrest import APIError

    _patch_authed(monkeypatch)

    def _boom(client, **kw):
        raise APIError({"message": "permission denied", "code": "42501"})

    monkeypatch.setattr(ex, "fetch_experiments", _boom)
    res = CliRunner().invoke(cli, ["cyl", "experiments", "list"])
    assert res.exit_code != 0
    assert "permission denied" in res.output  # clean message, not a raw traceback


def test_list_apierror_without_message_falls_back(monkeypatch):
    # APIError.message is None when the body has no "message" key; fall back to str(exc)
    # so the diagnostic survives instead of printing "Error: None".
    from postgrest import APIError

    _patch_authed(monkeypatch)

    def _boom(client, **kw):
        raise APIError({"code": "42P01", "details": "relation does not exist"})

    monkeypatch.setattr(ex, "fetch_experiments", _boom)
    res = CliRunner().invoke(cli, ["cyl", "experiments", "list"])
    assert res.exit_code != 0
    assert "None" not in res.output
    assert "42P01" in res.output or "relation does not exist" in res.output


def test_list_json_sorted(monkeypatch):
    _patch_authed(monkeypatch)
    monkeypatch.setattr(ex, "fetch_experiments", lambda client, **kw: EXPS)
    res = CliRunner().invoke(cli, ["cyl", "experiments", "list", "--json"])
    assert res.exit_code == 0, res.output
    payload = json.loads(res.output)
    assert [e["experiment_id"] for e in payload] == [3, 1, 2]  # sorted by species, then name
    assert payload[0] == {"species": "Canola", "experiment": "Alpha", "experiment_id": 3}


def test_list_empty(monkeypatch):
    _patch_authed(monkeypatch)
    monkeypatch.setattr(ex, "fetch_experiments", lambda client, **kw: [])
    res = CliRunner().invoke(cli, ["cyl", "experiments", "list"])
    assert res.exit_code == 0
    assert "No experiments found" in res.output


def test_list_empty_json(monkeypatch):
    _patch_authed(monkeypatch)
    monkeypatch.setattr(ex, "fetch_experiments", lambda client, **kw: [])
    res = CliRunner().invoke(cli, ["cyl", "experiments", "list", "--json"])
    assert res.exit_code == 0
    assert res.output.strip() == "[]"


# --- --species menu ---------------------------------------------------------


def test_list_species_menu_filters(monkeypatch):
    _patch_authed(monkeypatch)
    monkeypatch.setattr(
        ex, "fetch_species_with_experiments", lambda client: [(3, "Canola"), (2, "Rice")]
    )
    captured = {}

    def _fetch(client, *, species_id=None, limit=ex.DEFAULT_LIMIT):
        captured["species_id"] = species_id
        return [EXPS[1], EXPS[2]]

    monkeypatch.setattr(ex, "fetch_experiments", _fetch)
    # menu: 0) All  1) Canola  2) Rice → pick 1 → Canola (id 3)
    res = CliRunner().invoke(
        cli, ["cyl", "experiments", "list", "--species", "--output", "json"], input="1\n"
    )
    assert res.exit_code == 0, res.output
    assert captured["species_id"] == 3  # chosen menu entry → its species_id, passed to fetch


def test_list_species_menu_all_is_no_filter(monkeypatch):
    _patch_authed(monkeypatch)
    monkeypatch.setattr(ex, "fetch_species_with_experiments", lambda client: [(3, "Canola")])
    captured = {}

    def _fetch(client, *, species_id=None, limit=ex.DEFAULT_LIMIT):
        captured["species_id"] = species_id
        return EXPS

    monkeypatch.setattr(ex, "fetch_experiments", _fetch)
    res = CliRunner().invoke(cli, ["cyl", "experiments", "list", "--species"], input="0\n")
    assert res.exit_code == 0, res.output
    assert captured["species_id"] is None  # 0 = All species → no filter


def test_list_species_menu_none_available(monkeypatch):
    _patch_authed(monkeypatch)
    monkeypatch.setattr(ex, "fetch_species_with_experiments", lambda client: [])
    res = CliRunner().invoke(cli, ["cyl", "experiments", "list", "--species"], input="0\n")
    assert res.exit_code != 0
    assert "No species" in res.output


def test_list_species_menu_goes_to_stderr_stdout_stays_clean_json(monkeypatch):
    # The whole point of err=True: the menu must not contaminate machine output. A script
    # doing `--species --output json > data.json` must get pure JSON on stdout, menu on stderr.
    _patch_authed(monkeypatch)
    monkeypatch.setattr(ex, "fetch_species_with_experiments", lambda client: [(3, "Canola")])
    monkeypatch.setattr(ex, "fetch_experiments", lambda client, **kw: EXPS)
    res = CliRunner().invoke(
        cli, ["cyl", "experiments", "list", "--species", "--output", "json"], input="1\n"
    )
    assert res.exit_code == 0, res.output
    json.loads(res.stdout)  # stdout is valid JSON — would raise if the menu leaked into it
    assert "Select a species" not in res.stdout  # menu absent from stdout
    assert "Select a species" in res.stderr  # menu present on stderr


def test_list_species_menu_non_tty_aborts(monkeypatch):
    # No stdin to answer the menu (pipe/CI) → abort loudly rather than pick a wrong species.
    _patch_authed(monkeypatch)
    monkeypatch.setattr(ex, "fetch_species_with_experiments", lambda client: [(3, "Canola")])
    captured = {"fetched": False}

    def _fetch(client, **kw):
        captured["fetched"] = True
        return EXPS

    monkeypatch.setattr(ex, "fetch_experiments", _fetch)
    res = CliRunner().invoke(cli, ["cyl", "experiments", "list", "--species"], input="")
    assert res.exit_code != 0  # Abort → non-zero exit, not a silent wrong result
    assert captured["fetched"] is False  # never fetched with a guessed species


def test_list_species_menu_out_of_range_reprompts(monkeypatch):
    # Drive the REAL prompt (no monkeypatch of click.prompt) so IntRange validation runs:
    # an out-of-range entry is rejected and re-asked, then a valid one resolves correctly.
    _patch_authed(monkeypatch)
    monkeypatch.setattr(
        ex, "fetch_species_with_experiments", lambda client: [(3, "Canola"), (2, "Rice")]
    )
    captured = {}

    def _fetch(client, *, species_id=None, limit=ex.DEFAULT_LIMIT):
        captured["species_id"] = species_id
        return [EXPS[1]]

    monkeypatch.setattr(ex, "fetch_experiments", _fetch)
    # menu offers 0..2; "9" is rejected and re-prompts, then "2" → Rice (id 2)
    res = CliRunner().invoke(cli, ["cyl", "experiments", "list", "--species"], input="9\n2\n")
    assert res.exit_code == 0, res.output
    assert captured["species_id"] == 2  # resolved to the valid re-entry, did not crash
    assert "is not in the range" in res.stderr  # IntRange rejected the bad entry


# --- output formats ---------------------------------------------------------


def test_list_output_csv(monkeypatch):
    _patch_authed(monkeypatch)
    monkeypatch.setattr(ex, "fetch_experiments", lambda client, **kw: EXPS)
    res = CliRunner().invoke(cli, ["cyl", "experiments", "list", "--output", "csv"])
    assert res.exit_code == 0, res.output
    lines = res.output.strip().splitlines()
    assert lines[0] == "species,experiment,experiment_id"  # header
    assert lines[1] == "Canola,Alpha,3"  # sorted species/name → Canola/Alpha first


def test_list_output_json_equals_json_alias(monkeypatch):
    _patch_authed(monkeypatch)
    monkeypatch.setattr(ex, "fetch_experiments", lambda client, **kw: EXPS)
    a = CliRunner().invoke(cli, ["cyl", "experiments", "list", "--output", "json"])
    b = CliRunner().invoke(cli, ["cyl", "experiments", "list", "--json"])
    assert a.exit_code == 0 and a.output == b.output  # --json is an alias for --output json
    assert json.loads(a.output)[0]["experiment_id"] == 3


def test_list_json_and_conflicting_output_rejected(monkeypatch):
    _patch_authed(monkeypatch)
    monkeypatch.setattr(ex, "fetch_experiments", lambda client, **kw: EXPS)
    res = CliRunner().invoke(cli, ["cyl", "experiments", "list", "--json", "--output", "csv"])
    assert res.exit_code != 0
    assert "not both" in res.output.lower()


def test_species_conflict_validated_before_prompt(monkeypatch):
    # --species opens a menu that blocks on stdin. The --json/--output conflict must be caught
    # first (fail fast) — with no stdin, a wrong order would hang/abort instead of usage-erroring.
    _patch_authed(monkeypatch)
    monkeypatch.setattr(
        ex,
        "fetch_species_with_experiments",
        lambda client: (_ for _ in ()).throw(AssertionError("must not reach the menu")),
    )
    res = CliRunner().invoke(
        cli, ["cyl", "experiments", "list", "--species", "--json", "--output", "csv"]
    )
    assert res.exit_code != 0
    assert "not both" in res.output.lower()  # the conflict error, not an abort/hang


def test_list_warns_when_capped(monkeypatch):
    # fetch returns exactly `limit` rows → capped → a warning on stderr (not stdout).
    _patch_authed(monkeypatch)
    monkeypatch.setattr(ex, "fetch_experiments", lambda client, **kw: EXPS[:2])
    res = CliRunner().invoke(
        cli, ["cyl", "experiments", "list", "--limit", "2", "--output", "json"]
    )
    assert res.exit_code == 0, res.output
    assert "capped at --limit 2" in res.stderr  # warned
    assert "capped" not in res.stdout  # stdout stays clean JSON
    json.loads(res.stdout)


def test_list_no_warning_when_under_cap(monkeypatch):
    _patch_authed(monkeypatch)
    monkeypatch.setattr(ex, "fetch_experiments", lambda client, **kw: EXPS)  # 3 < limit
    res = CliRunner().invoke(cli, ["cyl", "experiments", "list", "--limit", "1000"])
    assert res.exit_code == 0, res.output
    assert "capped" not in res.stderr


def test_list_limit_passed_through_and_capped(monkeypatch):
    _patch_authed(monkeypatch)
    captured = {}

    def _fetch(client, *, species_id=None, limit=ex.DEFAULT_LIMIT):
        captured["limit"] = limit
        return EXPS

    monkeypatch.setattr(ex, "fetch_experiments", _fetch)

    ok = CliRunner().invoke(cli, ["cyl", "experiments", "list", "--limit", "5"])
    assert ok.exit_code == 0, ok.output
    assert captured["limit"] == 5

    # over the cap (DEFAULT_LIMIT) → rejected by IntRange before any fetch
    over = CliRunner().invoke(
        cli, ["cyl", "experiments", "list", "--limit", str(ex.DEFAULT_LIMIT + 1)]
    )
    assert over.exit_code != 0
    assert "range" in over.output.lower()


# --- grouping ---------------------------------------------------------------


def test_experiments_grouped_under_cyl_not_top_level():
    root = CliRunner().invoke(cli, ["--help"])
    assert "experiments" not in root.output  # not a top-level command
    sub = CliRunner().invoke(cli, ["cyl", "experiments", "--help"])
    assert "list" in sub.output
    assert "experiments" in CliRunner().invoke(cli, ["cyl", "--help"]).output
