"""The Cell Ranger template's de step: it runs `scrna-analysis de` after clustering in the analysis
image, build-h5ad waits for it so the final file holds its result, and the analysis image checks
its bloomctl can load that result."""

import re
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


def test_the_analysis_image_has_a_bloomctl_that_colours_any_number_of_clusters():
    # 0.1.0a14 and later load more than 23 clusters; the build checks it does.
    dockerfile = (ARGO / "analysis" / "Dockerfile").read_text()
    assert "cluster_colours(40)" in dockerfile


def test_the_analysis_image_has_a_bloomctl_that_uploads_counts_in_parallel():
    # 0.1.0a15 and later upload gene counts 8 at a time; the build checks it does.
    requirements = (ARGO / "analysis" / "requirements.txt").read_text().splitlines()
    assert "bloomctl==0.1.0a17" in requirements
    dockerfile = (ARGO / "analysis" / "Dockerfile").read_text()
    assert "_counts.UPLOAD_WORKERS == 8" in dockerfile


def test_every_analysis_step_uses_one_image():
    template = (ARGO / "cellranger" / "cellranger-count-template.yaml").read_text()
    assert set(re.findall(r"scrna-analysis:(\S+)", template)) == {"0.1.7"}
