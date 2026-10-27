"""Tests for the Cell Ranger template's build-reference step: its script, taken from the template
and run with a fake `cellranger` on PATH and the run's folders under a temporary directory. It
builds the reference from the downloaded genome, publishes it in one rename, keeps a finished
reference on a re-run, and fails clearly (3 no genome files, 6 bad input, 17 mkref failed)."""

import os
import stat
import subprocess
from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")

TEMPLATE = (
    Path(__file__).resolve().parents[2]
    / "argo"
    / "scrna"
    / "cellranger"
    / "cellranger-count-template.yaml"
)
GENOME = "athaliana_col0.v2"
RUN_ID = "col0__athaliana_col0.v2__u"

# Writes the reference mkref would, into ./<genome>, and records its arguments.
FAKE_CELLRANGER = r"""#!/usr/bin/env bash
if [ "$1" = "--version" ]; then echo "cellranger 10.1.0 (fake)"; exit 0; fi
printf '%s\n' "$@" > "${FAKE_OUT}"
[ -n "${FAKE_FAIL:-}" ] && { echo "mkref: bad GTF" >&2; exit 1; }
genome=""
for arg in "$@"; do case "$arg" in --genome=*) genome="${arg#--genome=}" ;; esac; done
mkdir -p "${genome}/star"
echo '{"genomes": ["'"${genome}"'"]}' > "${genome}/reference.json"
echo index > "${genome}/star/SA"
"""


def _script(shared: Path, work: Path) -> str:
    templates = yaml.safe_load(TEMPLATE.read_text())["spec"]["templates"]
    script = next(t for t in templates if t["name"] == "build-reference")["container"]["args"][0]
    # /work first, so a temporary path that happens to contain it isn't rewritten.
    return script.replace("/work", str(work)).replace("/shared/runs", str(shared))


@pytest.fixture
def env(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake = bin_dir / "cellranger"
    fake.write_text(FAKE_CELLRANGER)
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    shared, work = tmp_path / "shared" / "runs", tmp_path / "work"
    genome = shared / f"{RUN_ID}.genome" / "genome"
    genome.mkdir(parents=True)
    (genome / "genome.fa").write_text(">chr1\nACGT\n")
    (genome / "genes.gtf").write_text("chr1\tx\texon\t1\t4\t.\t+\t.\tgene_id \"g\";\n")
    work.mkdir()
    return {
        "env": {
            "PATH": f"{bin_dir}:{os.environ['PATH']}",
            "GENOME": GENOME,
            "RUN_ID": RUN_ID,
            "CORES": "8",
            "MEM_GB": "28",
            "WORKFLOW_NAME": "wf-1",
            "FAKE_OUT": str(tmp_path / "cellranger.out"),
        },
        "script": _script(shared, work),
        "shared": shared,
        "out": tmp_path / "cellranger.out",
    }


def _run(env, **extra):
    return subprocess.run(
        ["bash", "-c", env["script"]],
        env={**env["env"], **extra},
        capture_output=True,
        text=True,
    )


def _ref(env) -> Path:
    return env["shared"] / f"{RUN_ID}.genome" / "ref" / GENOME


def test_the_reference_is_built_from_the_downloaded_genome(env):
    result = _run(env)
    assert result.returncode == 0, result.stdout + result.stderr
    assert (_ref(env) / "reference.json").is_file()
    assert (_ref(env) / "star" / "SA").read_text() == "index\n"
    genome = env["shared"] / f"{RUN_ID}.genome" / "genome"
    assert env["out"].read_text().splitlines() == [
        "mkref",
        f"--genome={GENOME}",
        f"--fasta={genome}/genome.fa",
        f"--genes={genome}/genes.gtf",
        "--nthreads=8",
        "--memgb=28",
    ]


def test_nothing_partial_is_left_beside_the_reference(env):
    _run(env)
    assert [p.name for p in (env["shared"] / f"{RUN_ID}.genome" / "ref").iterdir()] == [GENOME]


def test_a_finished_reference_is_kept_without_running_mkref(env):
    _run(env)
    env["out"].unlink()
    result = _run(env)
    assert result.returncode == 0 and "already built" in result.stdout
    assert not env["out"].exists(), "mkref isn't run again"


def test_a_half_built_reference_and_an_old_partial_folder_are_replaced(env):
    ref = _ref(env)
    ref.mkdir(parents=True)
    (ref / "stale").write_text("x")
    partial = ref.parent / f".partial-{GENOME}-wf-1"
    partial.mkdir()
    (partial / "stale").write_text("x")
    assert _run(env).returncode == 0
    assert (ref / "reference.json").is_file() and not (ref / "stale").exists()
    assert not partial.exists()


def test_another_runs_partial_folder_is_left_alone(env):
    other = env["shared"] / f"{RUN_ID}.genome" / "ref" / f".partial-{GENOME}-wf-2"
    other.mkdir(parents=True)
    (other / "half").write_text("x")
    assert _run(env).returncode == 0
    assert (other / "half").exists(), "another workflow's build isn't touched"


def test_the_partial_folder_is_named_for_the_workflow(env):
    # Two runs on one run key share the run's folder; each builds in its own partial folder.
    assert 'tmp="/shared/runs/${RUN_ID}.genome/ref/.partial-${GENOME}-${WORKFLOW_NAME}"' in (
        next(
            t
            for t in yaml.safe_load(TEMPLATE.read_text())["spec"]["templates"]
            if t["name"] == "build-reference"
        )["container"]["args"][0]
    )


@pytest.mark.parametrize("missing", ["genome.fa", "genes.gtf"])
def test_a_missing_genome_file_fails_with_3(env, missing):
    (env["shared"] / f"{RUN_ID}.genome" / "genome" / missing).unlink()
    result = _run(env)
    assert result.returncode == 3 and f"no downloaded {missing}" in result.stdout
    assert not env["out"].exists()


def test_an_empty_genome_file_fails_with_3(env):
    (env["shared"] / f"{RUN_ID}.genome" / "genome" / "genes.gtf").write_text("")
    assert _run(env).returncode == 3


@pytest.mark.parametrize(
    "name, value", [("GENOME", ""), ("GENOME", ".."), ("GENOME", "a/b.v1"), ("RUN_ID", ""), ("RUN_ID", "a/b")]
)
def test_bad_input_fails_with_6_before_touching_anything(env, name, value):
    result = _run(env, **{name: value})
    assert result.returncode == 6
    assert not env["out"].exists()


def test_an_mkref_failure_fails_with_17_and_publishes_nothing(env):
    result = _run(env, FAKE_FAIL="1")
    assert result.returncode == 17 and "couldn't build a reference" in result.stdout
    assert not _ref(env).exists()


def test_the_step_isnt_retried_for_a_failure_a_retry_cant_fix():
    templates = yaml.safe_load(TEMPLATE.read_text())["spec"]["templates"]
    step = next(t for t in templates if t["name"] == "build-reference")
    rule = step["retryStrategy"]["expression"]
    for code in (3, 6, 17):
        assert f"asInt(lastRetry.exitCode) != {code}" in rule
    assert step["retryStrategy"]["limit"] == 1


def test_the_step_gets_its_resources_and_workflow_name():
    templates = {t["name"]: t for t in yaml.safe_load(TEMPLATE.read_text())["spec"]["templates"]}
    step = templates["build-reference"]
    env = {e["name"]: e["value"] for e in step["container"]["env"]}
    assert env["WORKFLOW_NAME"] == "{{workflow.name}}"
    assert env["CORES"] == "{{inputs.parameters.cores}}"
    assert env["MEM_GB"] == "{{inputs.parameters.mem-gb}}"
    patch = yaml.safe_load(step["podSpecPatch"])["containers"][0]["resources"]
    assert patch["limits"] == {
        "cpu": "{{inputs.parameters.cores}}",
        "memory": "{{inputs.parameters.memory}}",
    }
    tasks = {t["name"]: t for t in templates["sample-pipeline"]["dag"]["tasks"]}
    passed = {p["name"]: p["value"] for p in tasks["build-reference"]["arguments"]["parameters"]}
    assert passed["cores"] == "{{inputs.parameters.ref-cores}}"
    assert passed["memory"] == "{{inputs.parameters.ref-memory}}"
    assert passed["mem-gb"] == "{{inputs.parameters.ref-mem-gb}}"


def test_mkref_is_given_less_memory_than_the_pod_has():
    templates = {t["name"]: t for t in yaml.safe_load(TEMPLATE.read_text())["spec"]["templates"]}
    declared = {
        p["name"]: p.get("value") for p in templates["sample-pipeline"]["inputs"]["parameters"]
    }
    assert int(declared["ref-mem-gb"]) < int(declared["ref-memory"].removesuffix("Gi"))


def _cleanup(shared: Path, run_id: str):
    templates = yaml.safe_load(TEMPLATE.read_text())["spec"]["templates"]
    script = next(t for t in templates if t["name"] == "cleanup")["container"]["args"][0]
    return subprocess.run(
        ["bash", "-c", script.replace("/shared/runs", str(shared))],
        env={"PATH": os.environ["PATH"], "RUN_ID": run_id},
        capture_output=True,
        text=True,
    )


def test_cleanup_deletes_the_runs_folder_and_its_genome_folder(tmp_path):
    shared = tmp_path / "runs"
    for folder in (RUN_ID, f"{RUN_ID}.genome/ref/{GENOME}", "other_run.genome"):
        (shared / folder).mkdir(parents=True)
    result = _cleanup(shared, RUN_ID)
    assert result.returncode == 0, result.stdout + result.stderr
    assert sorted(p.name for p in shared.iterdir()) == ["other_run.genome"]


@pytest.mark.parametrize("run_id", ["", ".", "..", "a/b"])
def test_cleanup_refuses_a_bad_run_id(tmp_path, run_id):
    shared = tmp_path / "runs"
    (shared / "keep").mkdir(parents=True)
    assert _cleanup(shared, run_id).returncode == 1
    assert (shared / "keep").exists()
