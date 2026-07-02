"""bloomctl list — experiments + scans."""

from click.testing import CliRunner

import bloomctl.cli as climod
import bloomctl.download as dl
from bloomctl.cli import cli


def test_list_experiments(monkeypatch):
    monkeypatch.setattr(climod, "_authed_client", lambda profile: object())
    monkeypatch.setattr(
        dl, "fetch_experiments", lambda client: [
            {"id": 1, "name": "Experiment 1", "created_at": "2024-01-18T00:00:00Z"},
            {"id": 2, "name": "GIFTOL", "created_at": "2026-05-11T00:00:00Z"},
        ]
    )
    result = CliRunner().invoke(cli, ["list", "experiments"])
    assert result.exit_code == 0, result.output
    assert "Experiment 1" in result.output
    assert "GIFTOL" in result.output
    assert "2024-01-18" in result.output  # created_at truncated to the date


def test_list_experiments_empty(monkeypatch):
    monkeypatch.setattr(climod, "_authed_client", lambda profile: object())
    monkeypatch.setattr(dl, "fetch_experiments", lambda client: [])
    result = CliRunner().invoke(cli, ["list", "experiments"])
    assert result.exit_code == 0
    assert "No experiments found" in result.output


def test_list_scans(monkeypatch):
    monkeypatch.setattr(climod, "_authed_client", lambda profile: object())
    captured = {}

    def _fetch_scans(client, experiment_id, **kwargs):
        captured["experiment_id"] = experiment_id
        return [{"scan_id": 7, "qr_code": "SOY-W1-001", "wave_number": 1,
                 "plant_age_days": 6, "date_scanned": "2024-01-18"}]

    monkeypatch.setattr(dl, "fetch_scans", _fetch_scans)
    result = CliRunner().invoke(cli, ["list", "scans", "--experiment-id", "42"])
    assert result.exit_code == 0, result.output
    assert captured["experiment_id"] == 42
    assert "SOY-W1-001" in result.output


def test_list_scans_empty(monkeypatch):
    monkeypatch.setattr(climod, "_authed_client", lambda profile: object())
    monkeypatch.setattr(dl, "fetch_scans", lambda *a, **k: [])
    result = CliRunner().invoke(cli, ["list", "scans", "--experiment-id", "999"])
    assert result.exit_code == 0
    assert "No scans found for experiment 999" in result.output


def test_list_scans_requires_experiment_id():
    result = CliRunner().invoke(cli, ["list", "scans"])
    assert result.exit_code != 0  # --experiment-id is required
