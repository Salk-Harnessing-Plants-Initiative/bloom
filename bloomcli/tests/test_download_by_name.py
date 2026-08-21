"""Download by experiment name (`bloomctl cyl download --experiment-name`, server-side search)."""

from click.testing import CliRunner
from test_download_metadata import SCAN

import bloomctl.auth as auth
import bloomctl.cyl.download as dl
from bloomctl.cli import cli
from bloomctl.credentials import Credentials


def _row(id, name, species="Soybean"):
    return {
        "id": id,
        "name": name,
        "species_name": species,
        "created_at": "2026-01-15T00:00:00+00:00",
    }


def _auth(monkeypatch):
    monkeypatch.setattr(
        "bloomctl.credentials.load_credentials",
        lambda *a, **k: Credentials("https://x/api", "KEY", "u@s.edu", "pw"),
    )
    monkeypatch.setattr(auth, "make_authed_client", lambda creds: object())


def _no_download(monkeypatch):
    monkeypatch.setattr(
        dl,
        "fetch_scans",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not download")),
    )


# --- search_experiments (RPC call shape) ------------------------------------


def test_search_experiments_calls_rpc_with_bound_args():
    captured = {}

    class _Client:
        def rpc(self, fn, params):
            captured["fn"] = fn
            captured["params"] = params
            return self

        def execute(self):
            return type("R", (), {"data": [_row(2, "X")]})()

    out = dl.search_experiments(_Client(), "drought", species="Soybean")
    assert captured["fn"] == "cyl_experiment_search"
    assert captured["params"] == {"p_query": "drought", "p_species": "Soybean"}  # bound, not SQL
    assert out[0]["id"] == 2


def test_search_experiments_omits_species_when_none():
    captured = {}

    class _Client:
        def rpc(self, fn, params):
            captured["params"] = params
            return self

        def execute(self):
            return type("R", (), {"data": None})()

    assert dl.search_experiments(_Client(), "drought") == []
    assert captured["params"] == {"p_query": "drought"}  # no p_species key when unset


# --- command wiring ---------------------------------------------------------


def test_name_resolves_and_downloads(tmp_path, monkeypatch):
    _auth(monkeypatch)
    monkeypatch.setattr(
        dl,
        "search_experiments",
        lambda c, q, species=None: [_row(2, "Drought Response 2024", "Arabidopsis")],
    )
    captured = {}

    def _fetch_scans(client, experiment_id, **k):
        captured["experiment_id"] = experiment_id
        return [SCAN]

    monkeypatch.setattr(dl, "fetch_scans", _fetch_scans)
    monkeypatch.setattr(dl, "fetch_genotypes", lambda c, ids: {})

    res = CliRunner().invoke(
        cli,
        ["cyl", "download", str(tmp_path / "out"), "--experiment-name", "drought", "--meta-only"],
    )
    assert res.exit_code == 0, res.output
    assert captured["experiment_id"] == 2  # resolved id flows to the scan fetch
    assert "Matched: Drought Response 2024 (Arabidopsis) (id 2)" in res.stderr


def test_ambiguous_name_lists_candidates_and_downloads_nothing(tmp_path, monkeypatch):
    _auth(monkeypatch)
    _no_download(monkeypatch)
    monkeypatch.setattr(
        dl,
        "search_experiments",
        lambda c, q, species=None: [
            _row(6, "2025-11-20_soybean_cylinders"),
            _row(18, "2026-01-15 Cquesta soy"),
        ],
    )
    res = CliRunner().invoke(
        cli, ["cyl", "download", str(tmp_path / "out"), "--experiment-name", "soy", "--meta-only"]
    )
    assert res.exit_code != 0
    assert "2 experiments match" in res.output
    assert "2025-11-20_soybean_cylinders" in res.output  # candidate labels listed
    assert "Cquesta soy" in res.output
    # the candidate rows carry the id (to pass to --experiment-id) and the created date
    assert "6" in res.output and "18" in res.output
    assert "2026-01-15" in res.output


def test_search_apierror_mapped_to_clean_clickexception(tmp_path, monkeypatch):
    # The RPC RAISEs on a >200-char query (and on permission errors); that must surface as a
    # clean CLI message, not a raw postgrest traceback — matching accessions/datasets.
    from postgrest import APIError

    _auth(monkeypatch)
    _no_download(monkeypatch)

    def _boom(client, query, species=None):
        raise APIError({"message": "search query too long (max 200 characters)", "code": "P0001"})

    monkeypatch.setattr(dl, "search_experiments", _boom)
    res = CliRunner().invoke(
        cli, ["cyl", "download", str(tmp_path / "out"), "--experiment-name", "x" * 201]
    )
    assert res.exit_code != 0
    assert "search query too long" in res.output  # the mapped message
    assert "Could not read" not in res.output, (
        "P0001 is a sentence written for the user, so it must arrive unprefixed — the "
        "substring above is satisfied by the wrapped form too, so it cannot catch this alone"
    )
    assert isinstance(res.exception, SystemExit)  # click handled it; no raw traceback


def test_no_match_errors(tmp_path, monkeypatch):
    _auth(monkeypatch)
    _no_download(monkeypatch)
    monkeypatch.setattr(dl, "search_experiments", lambda c, q, species=None: [])
    res = CliRunner().invoke(
        cli, ["cyl", "download", str(tmp_path / "out"), "--experiment-name", "sunflower"]
    )
    assert res.exit_code != 0
    assert "No experiment matches" in res.output


def test_no_match_with_species_names_the_species(tmp_path, monkeypatch):
    # the no-match message scopes to the species when --species is given
    _auth(monkeypatch)
    _no_download(monkeypatch)
    monkeypatch.setattr(dl, "search_experiments", lambda c, q, species=None: [])
    res = CliRunner().invoke(
        cli,
        [
            "cyl",
            "download",
            str(tmp_path / "out"),
            "--experiment-name",
            "zzz",
            "--species",
            "Soybean",
        ],
    )
    assert res.exit_code != 0
    assert "No experiment matches 'zzz'" in res.output
    assert "for species 'Soybean'" in res.output


def test_species_reaches_the_search(tmp_path, monkeypatch):
    _auth(monkeypatch)
    captured = {}

    def _search(c, q, species=None):
        captured["q"] = q
        captured["species"] = species
        return [_row(18, "2026-01-15 Cquesta soy", "Soybean")]

    monkeypatch.setattr(dl, "search_experiments", _search)
    monkeypatch.setattr(dl, "fetch_scans", lambda client, eid, **k: [SCAN])
    monkeypatch.setattr(dl, "fetch_genotypes", lambda c, ids: {})

    res = CliRunner().invoke(
        cli,
        [
            "cyl",
            "download",
            str(tmp_path / "out"),
            "--experiment-name",
            "cquesta",
            "--species",
            "Soybean",
            "--meta-only",
        ],
    )
    assert res.exit_code == 0, res.output
    assert captured == {"q": "cquesta", "species": "Soybean"}  # both reach the search


def test_name_and_id_conflict_rejected(tmp_path):
    res = CliRunner().invoke(
        cli,
        [
            "cyl",
            "download",
            str(tmp_path / "out"),
            "--experiment-name",
            "x",
            "--experiment-id",
            "5",
        ],
    )
    assert res.exit_code != 0
    assert "exactly one" in res.output.lower()


def test_species_without_name_rejected(tmp_path):
    res = CliRunner().invoke(
        cli,
        ["cyl", "download", str(tmp_path / "out"), "--experiment-id", "5", "--species", "Soybean"],
    )
    assert res.exit_code != 0
    assert "only applies with --experiment-name" in res.output.lower()


# --- every metadata read names itself when it fails --------------------------
#
# `queried` wraps each read so the message says which one failed. Testing `queried`
# in isolation does not prove the call sites use it, so these drive the CLI.


def _raises(exc):
    def _boom(*a, **k):
        raise exc

    return _boom


def test_a_failed_scans_read_names_itself(tmp_path, monkeypatch):
    from postgrest import APIError

    _auth(monkeypatch)
    monkeypatch.setattr(
        dl, "fetch_scans", _raises(APIError({"message": "permission denied for view cyl_scans"}))
    )

    res = CliRunner().invoke(
        cli, ["cyl", "download", str(tmp_path / "out"), "--experiment-id", "17957"]
    )

    assert res.exit_code != 0
    assert isinstance(res.exception, SystemExit), "the APIError escaped click unhandled"
    assert "this experiment's scans" in res.output, "the message must say which read failed"
    assert "permission denied for view cyl_scans" in res.output


def test_a_failed_scan_read_names_itself(tmp_path, monkeypatch):
    from postgrest import APIError

    _auth(monkeypatch)
    _no_download(monkeypatch)
    monkeypatch.setattr(
        dl, "fetch_scan", _raises(APIError({"message": "permission denied for view cyl_scans"}))
    )

    res = CliRunner().invoke(cli, ["cyl", "download", str(tmp_path / "out"), "--scan-id", "77"])

    assert res.exit_code != 0
    assert isinstance(res.exception, SystemExit), "the APIError escaped click unhandled"
    assert "this scan" in res.output
    assert "permission denied for view cyl_scans" in res.output


def test_a_failed_genotype_read_names_itself(tmp_path, monkeypatch):
    from postgrest import APIError

    _auth(monkeypatch)
    monkeypatch.setattr(dl, "fetch_scans", lambda *a, **k: [SCAN])
    monkeypatch.setattr(
        dl, "fetch_genotypes", _raises(APIError({"message": "relation accessions does not exist"}))
    )

    res = CliRunner().invoke(
        cli, ["cyl", "download", str(tmp_path / "out"), "--experiment-id", "17957"]
    )

    assert res.exit_code != 0
    assert isinstance(res.exception, SystemExit), "the APIError escaped click unhandled"
    assert "the accession names" in res.output
    assert "relation accessions does not exist" in res.output


def test_a_read_timeout_resolving_a_name_is_a_sentence(tmp_path, monkeypatch):
    """The name search is the first read of a session, and it was the last one left bare."""
    import httpx

    _auth(monkeypatch)
    _no_download(monkeypatch)
    monkeypatch.setattr(dl, "search_experiments", _raises(httpx.ReadTimeout("")))

    res = CliRunner().invoke(
        cli, ["cyl", "download", str(tmp_path / "out"), "--experiment-name", "wave1"]
    )

    assert res.exit_code != 0
    assert isinstance(res.exception, SystemExit), "the ReadTimeout escaped click unhandled"
    assert "the experiment names" in res.output
    assert "check your connection" in res.output
