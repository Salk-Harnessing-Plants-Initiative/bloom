"""Tests for argo/scrna/analysis/load-dataset.sh, the Cell Ranger template's load-dataset step: it
loads the run's final .h5ad into Bloom with bloomctl (a fake on PATH here), keeps the
credentials out of the shared disk and its resume state on it, loads nothing for a run naming
no dataset, and fails clearly without credentials. Also: the template and the analysis image
run it."""

import os
import stat
import subprocess
from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")

ARGO = Path(__file__).resolve().parents[2] / "argo" / "scrna"
SCRIPT = ARGO / "analysis" / "load-dataset.sh"
TEMPLATE = ARGO / "cellranger" / "cellranger-count-template.yaml"
CREDENTIALS = "BLOOM_API_URL=https://bloom.test/api\nBLOOM_EMAIL=pipeline@bloom.test\n"

# Records what bloomctl was run with, where HOME pointed, and what it found there.
FAKE_BLOOMCTL = r"""#!/usr/bin/env bash
{
  printf 'args:'; printf ' [%s]' "$@"; printf '\n'
  echo "home: ${HOME}"
  echo "credentials: $(tr '\n' '|' < "${HOME}/.bloom/credentials.txt")"
  echo "credentials-mode: $(stat -c %a "${HOME}/.bloom/credentials.txt" 2>/dev/null || stat -f %Lp "${HOME}/.bloom/credentials.txt")"
  echo "state: $(cd "${HOME}/.bloom/scrna-uploads" && pwd -P)"
} > "${FAKE_OUT}"
exit "${FAKE_EXIT:-0}"
"""


@pytest.fixture
def env(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake = bin_dir / "bloomctl"
    fake.write_text(FAKE_BLOOMCTL)
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    shared = tmp_path / "shared" / "runs"
    run = shared / "col0__tair10__u"
    (run / "h5ad").mkdir(parents=True)
    (run / "h5ad" / "col0.h5ad").write_bytes(b"h5ad")
    credentials = tmp_path / "etc-bloom" / "credentials.txt"
    credentials.parent.mkdir()
    credentials.write_text(CREDENTIALS)
    return {
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "SHARED_RUNS": str(shared),
        "BLOOM_CREDENTIALS": str(credentials),
        "SAMPLE": "col0",
        "RUN_ID": "col0__tair10__u",
        "DATASET_NAME": "Root atlas",
        "SPECIES_NAME": "Arabidopsis",
        "FAKE_OUT": str(tmp_path / "bloomctl.out"),
    }


def _run(env, **extra):
    return subprocess.run(
        ["bash", str(SCRIPT)], env={**env, **extra}, capture_output=True, text=True
    )


def _bloomctl(env) -> dict:
    out = Path(env["FAKE_OUT"])
    if not out.exists():
        return {}
    return dict(line.split(": ", 1) for line in out.read_text().splitlines())


def test_the_runs_h5ad_is_loaded_as_its_dataset(env):
    result = _run(env)
    assert result.returncode == 0, result.stderr
    run = Path(env["SHARED_RUNS"]) / env["RUN_ID"]
    assert _bloomctl(env)["args"] == (
        f"[scrna] [hdf5] [upload] [{run}/h5ad/col0.h5ad] [--name] [Root atlas] "
        "[--species] [Arabidopsis] [--annotation] [leiden] [--create] [--yes]"
    )
    assert "Loading" in result.stdout and "Root atlas" in result.stdout


def test_the_credentials_stay_off_the_shared_disk(env):
    _run(env)
    seen = _bloomctl(env)
    assert seen["credentials"] == CREDENTIALS.replace("\n", "|"), "bloomctl gets the same login"
    assert not seen["home"].startswith(env["SHARED_RUNS"])
    assert seen["credentials-mode"] == "600"
    assert not list(Path(env["SHARED_RUNS"]).rglob("credentials.txt"))


def test_bloomctls_resume_state_is_on_the_runs_shared_folder(env):
    _run(env)
    state = Path(env["SHARED_RUNS"]) / env["RUN_ID"] / "bloomctl-uploads"
    assert Path(_bloomctl(env)["state"]) == state.resolve()
    assert stat.S_IMODE(state.stat().st_mode) == 0o700


@pytest.mark.parametrize("name", ["", None])
def test_a_run_naming_no_dataset_loads_nothing_and_succeeds(env, name):
    e = {k: v for k, v in env.items() if k != "DATASET_NAME"}
    result = _run(e, **({} if name is None else {"DATASET_NAME": name}))
    assert result.returncode == 0
    assert "names no dataset" in result.stdout
    assert _bloomctl(env) == {}


def test_no_credentials_fails_with_16(env, tmp_path):
    result = _run(env, BLOOM_CREDENTIALS=str(tmp_path / "missing.txt"))
    assert result.returncode == 16
    assert "no Bloom credentials" in result.stderr
    assert _bloomctl(env) == {}


def test_an_empty_credentials_file_fails_with_16(env):
    Path(env["BLOOM_CREDENTIALS"]).write_text("")
    assert _run(env).returncode == 16


@pytest.mark.parametrize("missing", ["SAMPLE", "RUN_ID", "SPECIES_NAME"])
def test_a_missing_input_fails_with_6(env, missing):
    result = _run({k: v for k, v in env.items() if k != missing})
    assert result.returncode == 6 and "required" in result.stderr


def test_a_run_with_no_final_h5ad_fails_with_6(env):
    result = _run(env, SAMPLE="other")
    assert result.returncode == 6 and "no final .h5ad" in result.stderr


def test_bloomctls_exit_code_is_the_steps(env):
    assert _run(env, FAKE_EXIT="1").returncode == 1


# --------------------------------------------------------------------------- #
# The template and the image
# --------------------------------------------------------------------------- #


def _templates():
    return {t["name"]: t for t in yaml.safe_load(TEMPLATE.read_text())["spec"]["templates"]}


def test_the_sample_pipeline_loads_after_build_h5ad_and_cleans_up_after_the_load():
    tasks = {t["name"]: t for t in _templates()["sample-pipeline"]["dag"]["tasks"]}
    assert tasks["load-dataset"]["depends"] == "build-h5ad"
    assert tasks["cleanup"]["depends"] == "load-dataset"
    # Nothing is skipped: the result leaves the cluster only through the load.
    assert not [name for name, task in tasks.items() if "when" in task]
    passed = {p["name"]: p["value"] for p in tasks["load-dataset"]["arguments"]["parameters"]}
    assert passed["dataset-name"] == "{{inputs.parameters.dataset-name}}"
    assert passed["species-name"] == "{{inputs.parameters.species-name}}"


def test_the_load_step_runs_the_script_in_the_analysis_image():
    step = _templates()["load-dataset"]
    container = step["container"]
    assert container["image"].startswith("ghcr.io/salk-harnessing-plants-initiative/scrna-analysis:")
    assert container["command"][-1] == "load-dataset"
    env = {e["name"]: e["value"] for e in container["env"]}
    assert env == {
        "SAMPLE": "{{inputs.parameters.sample}}",
        "RUN_ID": "{{inputs.parameters.run-id}}",
        "DATASET_NAME": "{{inputs.parameters.dataset-name}}",
        "SPECIES_NAME": "{{inputs.parameters.species-name}}",
    }
    assert step["activeDeadlineSeconds"] >= 3600


def test_the_load_step_doesnt_retry_failures_a_retry_cant_fix():
    rule = _templates()["load-dataset"]["retryStrategy"]["expression"]
    for code in (2, 6, 16):
        assert f"asInt(lastRetry.exitCode) != {code}" in rule


def test_the_load_step_waits_out_bloomctls_unknown_write_hold_before_retrying():
    # bloomctl refuses to resume for 330 s after a write it couldn't confirm.
    backoff = _templates()["load-dataset"]["retryStrategy"]["backoff"]
    assert backoff == {"duration": "6m", "factor": 1}


def test_every_analysis_step_uses_the_same_image():
    images = {
        t["container"]["image"]
        for name, t in _templates().items()
        if name in ("preprocess", "cluster", "build-h5ad", "load-dataset")
    }
    assert len(images) == 1


def test_the_analysis_image_installs_the_script_and_a_pinned_bloomctl():
    dockerfile = (ARGO / "analysis" / "Dockerfile").read_text()
    assert "COPY load-dataset.sh /usr/local/bin/load-dataset" in dockerfile
    assert "/usr/local/bin/load-dataset" in dockerfile.split("chmod +x", 1)[1].split("\n")[0]
    pins = (ARGO / "analysis" / "requirements.txt").read_text().splitlines()
    assert [p for p in pins if p.startswith("bloomctl")] == ["bloomctl==0.1.0a10"]
