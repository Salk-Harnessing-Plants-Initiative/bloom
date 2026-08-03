"""Download by experiment name (`bloomctl cyl download --experiment-name`)."""

from click.testing import CliRunner
from test_download_metadata import SCAN

import bloomctl.auth as auth
import bloomctl.cyl.download as dl
import bloomctl.cyl.experiments as ex
from bloomctl.cli import cli
from bloomctl.credentials import Credentials

EXPS = [
    {"id": 2, "name": "Drought Response 2024", "species": {"common_name": "Arabidopsis"}},
    {"id": 6, "name": "2025-11-20_soybean_cylinders", "species": {"common_name": "Soybean"}},
    {"id": 18, "name": "2026-01-15 Cquesta soy", "species": {"common_name": "Soybean"}},
]


def _auth(monkeypatch):
    monkeypatch.setattr(
        "bloomctl.credentials.load_credentials",
        lambda *a, **k: Credentials("https://x/api", "KEY", "u@s.edu", "pw"),
    )
    monkeypatch.setattr(auth, "make_authed_client", lambda creds: object())
    monkeypatch.setattr(ex, "fetch_experiments", lambda client, **k: EXPS)


def _no_download(monkeypatch):
    monkeypatch.setattr(
        dl,
        "fetch_scans",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not download")),
    )


def test_name_resolves_and_downloads(tmp_path, monkeypatch):
    _auth(monkeypatch)
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
    assert captured["experiment_id"] == 2  # resolved id flows to the experiment fetch
    assert "Matched: Drought Response 2024 (Arabidopsis) (id 2)" in res.stderr


def test_ambiguous_name_lists_candidates_and_downloads_nothing(tmp_path, monkeypatch):
    _auth(monkeypatch)
    _no_download(monkeypatch)
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
    res = CliRunner().invoke(
        cli, ["cyl", "download", str(tmp_path / "out"), "--experiment-name", "sunflower"]
    )
    assert res.exit_code != 0
    assert "No experiment matches" in res.output


def test_species_narrows_name(tmp_path, monkeypatch):
    _auth(monkeypatch)
    captured = {}

    def _fetch_scans(client, experiment_id, **k):
        captured["experiment_id"] = experiment_id
        return [SCAN]

    monkeypatch.setattr(dl, "fetch_scans", _fetch_scans)
    monkeypatch.setattr(dl, "fetch_genotypes", lambda c, ids: {})

    # "cquesta" + --species Soybean → the single Soybean match (id 18)
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
    assert captured["experiment_id"] == 18


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
