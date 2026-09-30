"""Tests for argo/scrna/cellranger/fastq-sample-prefix.sh, which reads the FASTQ name prefix
that `cellranger count --sample` needs from a folder's Illumina-named files, so the files
needn't be named after the sample folder."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

SCRIPT = (
    Path(__file__).resolve().parents[2] / "argo/scrna/cellranger/fastq-sample-prefix.sh"
)
EXIT_BAD_FASTQ_NAMES = 7


def _bash() -> str | None:
    """A bash with associative arrays (4+), as in the pipeline image."""
    bash = shutil.which("bash")
    if bash is None:
        return None
    version = subprocess.run(
        [bash, "-c", "echo ${BASH_VERSINFO[0]}"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    return bash if int(version) >= 4 else None


BASH = _bash()
pytestmark = pytest.mark.skipif(
    BASH is None, reason="needs bash 4+ (the pipeline image has it)"
)


def run(folder: Path, names: list[str]) -> subprocess.CompletedProcess:
    folder.mkdir(parents=True, exist_ok=True)
    for name in names:
        (folder / name).touch()
    return subprocess.run(
        [BASH, str(SCRIPT), str(folder)], capture_output=True, text=True, check=False
    )


def pair(prefix: str, lane: int = 1, sample_number: int = 1) -> list[str]:
    return [
        f"{prefix}_S{sample_number}_L{lane:03}_R1_001.fastq.gz",
        f"{prefix}_S{sample_number}_L{lane:03}_R2_001.fastq.gz",
    ]


def test_a_prefix_other_than_the_folder_name_is_found(tmp_path):
    result = run(tmp_path / "root_rep1", pair("L007-259", lane=2))
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "L007-259"


def test_several_lanes_of_one_prefix_give_it_once(tmp_path):
    result = run(tmp_path, pair("tinygex", 1) + pair("tinygex", 2) + pair("tinygex", 3))
    assert (result.returncode, result.stdout.strip()) == (0, "tinygex")


def test_index_reads_are_allowed_but_not_needed(tmp_path):
    names = pair("tinygex") + [
        "tinygex_S1_L001_I1_001.fastq.gz",
        "tinygex_S1_L001_I2_001.fastq.gz",
    ]
    assert run(tmp_path, names).stdout.strip() == "tinygex"


def test_several_prefixes_are_passed_together(tmp_path):
    result = run(tmp_path, pair("L008-101") + pair("L007-259", sample_number=2))
    assert result.returncode == 0, result.stderr
    assert sorted(result.stdout.strip().split(",")) == ["L007-259", "L008-101"]


@pytest.mark.parametrize(
    "prefix", ["Col-0_root_rep1", "a_S1_b", "sample.with.dots", "SRR12046049"]
)
def test_prefixes_with_underscores_dots_or_accessions_are_read_whole(tmp_path, prefix):
    assert run(tmp_path, pair(prefix)).stdout.strip() == prefix


@pytest.mark.parametrize(
    "names",
    [
        ["SRR12046049_1.fastq.gz", "SRR12046049_2.fastq.gz"],
        ["reads_1.fastq.gz"] + pair("tinygex"),
        ["tinygex_R1.fastq.gz", "tinygex_R2.fastq.gz"],
        ["tinygex_S1_R1_001.fastq.gz", "tinygex_S1_R2_001.fastq.gz"],
        ["tinygex_S1_L001_R3_001.fastq.gz"] + pair("tinygex"),
    ],
    ids=[
        "sra-dump-names",
        "stray-fastq",
        "no-sample-or-lane",
        "no-lane",
        "unknown-read",
    ],
)
def test_files_not_named_the_illumina_way_are_refused_and_listed(tmp_path, names):
    result = run(tmp_path, names)
    assert result.returncode == EXIT_BAD_FASTQ_NAMES
    assert "aren't named like" in result.stderr
    bad = [n for n in names if n.endswith(".fastq.gz") and n not in pair("tinygex")]
    for name in bad:
        assert name in result.stderr


def test_uncompressed_fastqs_are_read_too(tmp_path):
    names = ["L007-259_S1_L002_R1_001.fastq", "L007-259_S1_L002_R2_001.fastq"]
    assert run(tmp_path, names).stdout.strip() == "L007-259"


def test_compressed_and_uncompressed_reads_of_one_lane_count_together(tmp_path):
    names = ["tinygex_S1_L001_R1_001.fastq.gz", "tinygex_S1_L001_R2_001.fastq"]
    assert run(tmp_path, names).stdout.strip() == "tinygex"


def test_an_uncompressed_misnamed_fastq_is_refused(tmp_path):
    result = run(tmp_path, ["SRR123_1.fastq"] + pair("tinygex"))
    assert result.returncode == EXIT_BAD_FASTQ_NAMES
    assert "SRR123_1.fastq" in result.stderr


def test_a_file_other_than_fastq_is_ignored(tmp_path):
    result = run(tmp_path, pair("tinygex") + ["checksum.txt", "reads_1.fq.gz"])
    assert (result.returncode, result.stdout.strip()) == (0, "tinygex")


@pytest.mark.parametrize(
    "names, missing",
    [
        (["tinygex_S1_L001_R2_001.fastq.gz"], "tinygex_L001_R1"),
        (["tinygex_S1_L001_R1_001.fastq.gz"], "tinygex_L001_R2"),
        (pair("tinygex", 1) + ["tinygex_S1_L002_R2_001.fastq.gz"], "tinygex_L002_R1"),
        (["tinygex_S1_L001_I1_001.fastq.gz"], "tinygex_L001_R1 tinygex_L001_R2"),
    ],
    ids=["no-r1", "no-r2", "second-lane-no-r1", "index-only"],
)
def test_a_lane_without_r1_or_r2_is_refused(tmp_path, names, missing):
    result = run(tmp_path, names)
    assert result.returncode == EXIT_BAD_FASTQ_NAMES
    assert "needs an R1" in result.stderr
    assert missing in result.stderr


def test_an_empty_folder_is_refused(tmp_path):
    result = run(tmp_path / "empty", [])
    assert result.returncode == EXIT_BAD_FASTQ_NAMES
    assert "no FASTQs" in result.stderr


def test_the_image_installs_it_and_run_count_uses_it():
    root = SCRIPT.parents[1]
    dockerfile = (root / "Dockerfile").read_text()
    assert (
        "COPY cellranger/fastq-sample-prefix.sh /usr/local/bin/fastq-sample-prefix"
        in dockerfile
    )
    assert (
        "/usr/local/bin/fastq-sample-prefix"
        in dockerfile.split("RUN chmod +x", 1)[1].split("\n")[0]
    )
    run_count = (root / "cellranger/run-count.sh").read_text()
    assert 'fastq-sample-prefix "${FASTQ_DIR}"' in run_count
    assert '--sample="${FASTQ_SAMPLE}"' in run_count
