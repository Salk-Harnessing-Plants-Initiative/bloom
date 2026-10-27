"""Tests for argo/scrna/cellranger/claim-run-dir.sh, which makes a run's folder on the shared disk
this run's own before fetch-sra or stage writes reads into it: the same inputs keep everything,
other inputs set the folder aside first."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "argo/scrna/cellranger/claim-run-dir.sh"


def claim(run_dir, inputs):
    return subprocess.run(["bash", str(SCRIPT), str(run_dir), inputs],
                          capture_output=True, text=True, check=False)


@pytest.fixture
def earlier(tmp_path):
    """A failed run's folder: its reads, a lane marker, its logs and its inputs."""
    run_dir = tmp_path / "runs/key"
    (run_dir / "fastq/s").mkdir(parents=True)
    (run_dir / "fastq/s/s_S1_L001_R1_001.fastq.gz").write_bytes(b"reads")
    (run_dir / "fastq/s/.L001").write_text("SRR1000001\n")
    (run_dir / "logs").mkdir()
    (run_dir / "logs/count.log").write_text("failed")
    (run_dir / ".inputs").write_text("sra SRR1000001\n")
    return run_dir


@pytest.mark.parametrize(
    "run_dir, inputs", [("", "sra SRR1"), ("{dir}", ""), ("{dir}", "sra SRR1\nsra SRR2")],
    ids=["no-folder", "no-inputs", "two-lines"],
)
def test_bad_arguments_are_refused_with_6(tmp_path, run_dir, inputs):
    result = claim(run_dir.format(dir=tmp_path / "runs/key") if run_dir else "", inputs)
    assert result.returncode == 6
    assert "usage" in result.stderr
    assert not (tmp_path / "runs").exists()


def test_a_new_folder_records_its_inputs_and_sets_nothing_aside(tmp_path):
    run_dir = tmp_path / "runs/key"
    assert claim(run_dir, "sra SRR1000001").returncode == 0
    assert (run_dir / ".inputs").read_text() == "sra SRR1000001\n"
    assert not list(run_dir.parent.glob("key.failed-*"))


def test_the_same_inputs_keep_everything_so_a_retry_resumes(earlier):
    before = sorted(p.relative_to(earlier) for p in earlier.rglob("*"))
    assert claim(earlier, "sra SRR1000001").returncode == 0
    assert sorted(p.relative_to(earlier) for p in earlier.rglob("*")) == before
    assert not list(earlier.parent.glob("key.failed-*"))


@pytest.mark.parametrize("inputs", ["sra SRR1000002", "sra SRR1000002 SRR1000001",
                                    "s3://lab-data/run42/ col0_S1_L001_R1_001.fastq.gz"])
def test_other_inputs_delete_the_reads_and_set_the_rest_aside(earlier, inputs):
    result = claim(earlier, inputs)
    assert result.returncode == 0
    assert "Set aside an earlier run's folder" in result.stdout
    assert sorted(p.name for p in earlier.iterdir()) == [".inputs"]
    assert (earlier / ".inputs").read_text() == f"{inputs}\n"
    [aside] = earlier.parent.glob("key.failed-*")
    assert (aside / "logs/count.log").read_text() == "failed"
    assert not (aside / "fastq").exists(), "an earlier run's reads are deleted, not kept"


def test_a_folder_without_a_record_of_its_inputs_is_set_aside(earlier):
    (earlier / ".inputs").unlink()
    assert claim(earlier, "sra SRR1000001").returncode == 0
    assert not (earlier / "fastq").exists()
    assert len(list(earlier.parent.glob("key.failed-*"))) == 1
