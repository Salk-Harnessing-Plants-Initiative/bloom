"""Tests for argo/scrna/fastq_qc/fastq_qc.py on small made-up FASTQs.

The full QC pass reads every read of every file; it must keep only the R1 barcodes it uses,
or a real sample (100M+ reads) runs out of memory.
"""

from __future__ import annotations

import gzip
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

QC_DIR = Path(__file__).resolve().parents[2] / "argo/scrna/fastq_qc"
SCRIPT = QC_DIR / "fastq_qc.py"

spec = importlib.util.spec_from_file_location("fastq_qc", SCRIPT)
fastq_qc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fastq_qc)

BARCODE = "AAACCCAAGAAACACT"


def write_fastq(path: Path, reads: int, length: int, prefix: str = "") -> Path:
    body = prefix + "C" * (length - len(prefix))
    with gzip.open(path, "wt") as f:
        for n in range(reads):
            f.write(f"@r{n}\n{body}\n+\n{'I' * length}\n")
    return path


def test_a_full_pass_counts_every_read_but_keeps_only_the_barcodes_asked_for(tmp_path):
    path = write_fastq(tmp_path / "s_S1_L001_R1_001.fastq.gz", 5000, 28, BARCODE)
    stats, barcodes = fastq_qc.read_stats(str(path), None, keep_barcodes=100)
    assert stats["reads"] == 5000
    assert len(barcodes) == 100
    assert barcodes[0] == BARCODE


def test_files_other_than_r1_keep_no_barcodes(tmp_path):
    path = write_fastq(tmp_path / "s_S1_L001_R2_001.fastq.gz", 3000, 91)
    stats, barcodes = fastq_qc.read_stats(str(path), None)
    assert stats == {"reads": 3000, "min_len": 91, "max_len": 91, "mean_len": 91.0}
    assert barcodes == []


def test_a_quick_pass_stops_at_the_sample(tmp_path):
    path = write_fastq(tmp_path / "s_S1_L001_R1_001.fastq.gz", 3000, 28, BARCODE)
    stats, barcodes = fastq_qc.read_stats(str(path), 200, keep_barcodes=200)
    assert stats["reads"] == 200
    assert len(barcodes) == 200


@pytest.fixture
def whitelists(tmp_path):
    folder = tmp_path / "barcodes"
    folder.mkdir()
    for whitelist in fastq_qc.WHITELISTS:
        path = folder / whitelist["file"]
        text = (
            f"{BARCODE}\n"
            if whitelist["file"].startswith("3M-february-2018")
            else "GGGGGGGGGGGGGGGG\n"
        )
        if path.suffix == ".gz":
            with gzip.open(path, "wt") as f:
                f.write(text)
        else:
            path.write_text(text)
    return folder


def run_qc(fastqs: Path, whitelists: Path, out: Path, *extra: str) -> dict:
    """The run's summary; QC exits non-zero when it finds a problem, which the summary lists."""
    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--fastq-dir",
            str(fastqs),
            "--whitelist-dir",
            str(whitelists),
            "--out-dir",
            str(out),
            *extra,
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    summary = json.loads((out / "qc_summary.json").read_text())
    assert result.returncode == (0 if summary["passed"] else 1), result.stderr
    return summary


def test_the_full_pass_counts_both_lanes_and_guesses_from_the_first_r1_barcodes(
    tmp_path, whitelists
):
    fastqs = tmp_path / "fastq"
    fastqs.mkdir()
    for lane, reads in (("L001", 300), ("L002", 400)):
        write_fastq(fastqs / f"s_S1_{lane}_R1_001.fastq.gz", reads, 28, BARCODE)
        write_fastq(fastqs / f"s_S1_{lane}_R2_001.fastq.gz", reads, 91)
        write_fastq(fastqs / f"s_S1_{lane}_I1_001.fastq.gz", reads, 8)
    summary = run_qc(fastqs, whitelists, tmp_path / "out", "--sample-reads", "500")
    assert summary["read_pairs"] == 700
    assert summary["chemistry_guess"]["chemistry"] == "SC3Pv3-polyA"
    assert summary["whitelist_scores"][0]["share_of_barcodes"] == 1.0
    assert summary["passed"], summary["problems"]


def test_a_lane_with_mismatched_read_counts_is_a_problem(tmp_path, whitelists):
    fastqs = tmp_path / "fastq"
    fastqs.mkdir()
    write_fastq(fastqs / "s_S1_L001_R1_001.fastq.gz", 100, 28, BARCODE)
    write_fastq(fastqs / "s_S1_L001_R2_001.fastq.gz", 90, 91)
    summary = run_qc(fastqs, whitelists, tmp_path / "out", "--sample-reads", "50")
    assert "lane 001: R1 has 100 reads but R2 has 90" in summary["problems"]
