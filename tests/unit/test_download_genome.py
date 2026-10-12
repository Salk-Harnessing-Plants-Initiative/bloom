"""Tests for argo/scrna/analysis/download-genome.sh, the Cell Ranger template's download-genome
step: it downloads the run's genome version from Bloom with bloomctl (a fake on PATH here) into
the run's folder, unzipped and with the exact version recorded, keeps the credentials off the
shared disk, refuses bad input, and passes bloomctl's exit code on so only a network error is
retried. Also: the template wires the download, the reference build and count together."""

import os
import stat
import subprocess
from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")

ARGO = Path(__file__).resolve().parents[2] / "argo" / "scrna"
SCRIPT = ARGO / "analysis" / "download-genome.sh"
TEMPLATE = ARGO / "cellranger" / "cellranger-count-template.yaml"
CREDENTIALS = "BLOOM_API_URL=https://bloom.test/api\nBLOOM_EMAIL=pipeline@bloom.test\n"
RUN_ID = "col0__athaliana_col0.v2__u"

# Records what bloomctl was run with, where HOME pointed, and what it found there.
FAKE_BLOOMCTL = r"""#!/usr/bin/env bash
{
  printf 'args:'; printf ' [%s]' "$@"; printf '\n'
  echo "home: ${HOME}"
  echo "credentials: $(tr '\n' '|' < "${HOME}/.bloom/credentials.txt")"
  echo "credentials-mode: $(stat -c %a "${HOME}/.bloom/credentials.txt" 2>/dev/null || stat -f %Lp "${HOME}/.bloom/credentials.txt")"
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
    credentials = tmp_path / "etc-bloom" / "credentials.txt"
    credentials.parent.mkdir()
    credentials.write_text(CREDENTIALS)
    return {
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "SHARED_RUNS": str(tmp_path / "shared" / "runs"),
        "BLOOM_CREDENTIALS": str(credentials),
        "GENOME": "athaliana_col0.v2",
        "RUN_ID": RUN_ID,
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


def _templates():
    return {t["name"]: t for t in yaml.safe_load(TEMPLATE.read_text())["spec"]["templates"]}


# --------------------------------------------------------------------------- #
# The script
# --------------------------------------------------------------------------- #


def test_the_version_is_downloaded_unzipped_into_the_runs_folder(env):
    result = _run(env)
    assert result.returncode == 0, result.stderr
    dest = Path(env["SHARED_RUNS"]) / RUN_ID / "genome"
    assert _bloomctl(env)["args"] == (
        f"[genome] [download] [athaliana_col0.v2] [--to] [{dest}] [--unzip] "
        f"[--version-file] [{dest}/VERSION]"
    )
    assert dest.is_dir()


def test_the_credentials_stay_off_the_shared_disk(env):
    _run(env)
    seen = _bloomctl(env)
    assert seen["credentials"] == CREDENTIALS.replace("\n", "|"), "bloomctl gets the same login"
    assert not seen["home"].startswith(env["SHARED_RUNS"])
    assert seen["credentials-mode"] == "600"


@pytest.mark.parametrize(
    "genome",
    ["", "athaliana_col0", "Athaliana.v2", "athaliana_col0.v", "../x.v1", "a/b.v1", "x.v1 "],
    ids=["empty", "no-version", "uppercase", "no-number", "dotdot", "slash", "space"],
)
def test_a_genome_that_isnt_a_version_fails_with_6(env, genome):
    result = _run(env, GENOME=genome)
    assert result.returncode == 6
    assert _bloomctl(env) == {}, "bloomctl is never run"


@pytest.mark.parametrize("run_id", ["", ".", "..", "a/b"])
def test_a_bad_run_id_fails_with_6(env, run_id):
    assert _run(env, RUN_ID=run_id).returncode == 6
    assert _bloomctl(env) == {}


def test_no_credentials_fails_with_16(env, tmp_path):
    result = _run(env, BLOOM_CREDENTIALS=str(tmp_path / "missing.txt"))
    assert result.returncode == 16 and "no Bloom credentials" in result.stderr
    assert _bloomctl(env) == {}


def test_an_empty_credentials_file_fails_with_16(env):
    Path(env["BLOOM_CREDENTIALS"]).write_text("")
    assert _run(env).returncode == 16


@pytest.mark.parametrize("code", [1, 2, 3])
def test_bloomctls_exit_code_is_the_steps(env, code):
    assert _run(env, FAKE_EXIT=str(code)).returncode == code


# --------------------------------------------------------------------------- #
# The template
# --------------------------------------------------------------------------- #


def test_count_waits_for_the_reads_and_the_built_reference():
    tasks = {t["name"]: t for t in _templates()["sample-pipeline"]["dag"]["tasks"]}
    assert "depends" not in tasks["download-genome"], "it starts with the run"
    assert tasks["build-reference"]["depends"] == "download-genome"
    assert tasks["count"]["depends"] == "qc && build-reference"


def test_the_download_and_build_get_the_runs_genome_and_folder():
    tasks = {t["name"]: t for t in _templates()["sample-pipeline"]["dag"]["tasks"]}
    for step in ("download-genome", "build-reference"):
        params = {p["name"]: p["value"] for p in tasks[step]["arguments"]["parameters"]}
        assert params["genome"] == "{{inputs.parameters.reference}}"
        assert params["run-id"] == "{{inputs.parameters.run-id}}"


def test_count_reads_the_reference_built_in_the_runs_folder():
    env = {e["name"]: e["value"] for e in _templates()["count"]["container"]["env"]}
    assert env["REF_DIR"] == (
        "/shared/runs/{{inputs.parameters.run-id}}/ref/{{inputs.parameters.reference}}"
    )


def test_the_download_runs_the_script_in_the_analysis_image():
    step = _templates()["download-genome"]
    assert step["container"]["image"] == _templates()["load-dataset"]["container"]["image"]
    assert step["container"]["command"][-1] == "download-genome"
    env = {e["name"]: e["value"] for e in step["container"]["env"]}
    assert env == {
        "GENOME": "{{inputs.parameters.genome}}",
        "RUN_ID": "{{inputs.parameters.run-id}}",
    }


def test_only_a_network_error_retries_the_download():
    assert (
        _templates()["download-genome"]["retryStrategy"]["expression"]
        == "asInt(lastRetry.exitCode) == 1"
    )


def test_the_build_runs_mkref_in_the_cellranger_image_and_isnt_retried_without_a_genome():
    step = _templates()["build-reference"]
    assert step["container"]["image"] == _templates()["count"]["container"]["image"]
    assert "cellranger mkref" in step["container"]["args"][0]
    assert step["retryStrategy"]["expression"] == "asInt(lastRetry.exitCode) != 3"


def test_the_build_takes_its_resources_from_parameters_with_defaults():
    declared = {
        p["name"]: p.get("value") for p in _templates()["sample-pipeline"]["inputs"]["parameters"]
    }
    assert {k: declared[k] for k in ("ref-cores", "ref-memory", "ref-mem-gb")} == {
        "ref-cores": "8",
        "ref-memory": "32Gi",
        "ref-mem-gb": "28",
    }


def test_no_step_reads_a_reference_from_s3_or_a_kept_folder():
    text = TEMPLATE.read_text()
    assert "reference_genome/" not in text and "/shared/ref" not in text
    assert "stage-reference" not in _templates()


def test_the_analysis_image_installs_the_download_script():
    dockerfile = (ARGO / "analysis" / "Dockerfile").read_text()
    assert "COPY download-genome.sh /usr/local/bin/download-genome" in dockerfile
    assert "/usr/local/bin/download-genome" in dockerfile.split("chmod +x", 1)[1].split("RUN")[0]
    assert "bloomctl genome download --help" in dockerfile
