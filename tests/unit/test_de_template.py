"""The Cell Ranger template's de step: it runs `scrna-analysis de` after clustering in the analysis
image, build-h5ad waits for it so the final file holds its result, and the analysis image checks
its bloomctl can load that result."""

from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")

ARGO = Path(__file__).resolve().parents[2] / "argo" / "scrna"
TEMPLATE = ARGO / "cellranger" / "cellranger-count-template.yaml"


def _templates() -> dict:
    return {t["name"]: t for t in yaml.safe_load(TEMPLATE.read_text())["spec"]["templates"]}


def _tasks() -> dict:
    return {t["name"]: t for t in _templates()["sample-pipeline"]["dag"]["tasks"]}


def test_de_runs_after_clustering_and_build_waits_for_it():
    tasks = _tasks()
    assert tasks["de"]["template"] == "de"
    assert tasks["de"]["depends"] == "cluster"
    assert tasks["build-h5ad"]["depends"] == "cluster && de"


def test_de_gets_the_runs_folder():
    params = {p["name"]: p["value"] for p in _tasks()["de"]["arguments"]["parameters"]}
    assert params == {"run-id": "{{inputs.parameters.run-id}}"}


def test_de_runs_the_de_step_in_the_analysis_image():
    templates = _templates()
    container = templates["de"]["container"]
    assert container["image"] == templates["cluster"]["container"]["image"]
    assert container["command"][-1] == "scrna-analysis"
    assert container["args"] == ["de", "--root", "/shared/runs/{{inputs.parameters.run-id}}"]


def test_de_is_retried_once_like_the_other_analysis_steps():
    templates = _templates()
    assert templates["de"]["retryStrategy"] == templates["cluster"]["retryStrategy"]


def test_the_analysis_image_checks_its_bloomctl_loads_de():
    dockerfile = (ARGO / "analysis" / "Dockerfile").read_text()
    assert "bloomctl scrna hdf5 upload --help | grep -q -- --de-key" in dockerfile
