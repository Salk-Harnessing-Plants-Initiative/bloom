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


def test_no_match_errors(tmp_path, monkeypatch):
    _auth(monkeypatch)
    _no_download(monkeypatch)
    monkeypatch.setattr(dl, "search_experiments", lambda c, q, species=None: [])
    res = CliRunner().invoke(
        cli, ["cyl", "download", str(tmp_path / "out"), "--experiment-name", "sunflower"]
    )
    assert res.exit_code != 0
    assert "No experiment matches" in res.output


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
