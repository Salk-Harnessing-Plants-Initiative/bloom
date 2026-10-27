"""Tests for argo/scrna/fastq_qc/qc_report.py: the README a scientist reads for one sample's QC."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "argo/scrna/fastq_qc/qc_report.py"

ARC = {"file": "737K-arc-v1.txt.gz", "chemistry": "ARC-v1", "kit": "Multiome GEX", "r1_needed": 28}
V3 = {"file": "3M-february-2018_TRU.txt.gz", "chemistry": "SC3Pv3-polyA", "kit": "3' v3/v3.1", "r1_needed": 28}


def readme(tmp_path: Path, **summary) -> str:
    base = {
        "fastq_prefix": "s",
        "lanes": ["L001"],
        "read_pairs": 100,
        "r1_length": {"min": 28, "max": 28},
        "r2_length": {"min": 90, "max": 90},
        "chemistry_guess": None,
        "chemistry_named": None,
        "whitelist_scores": [{**V3, "share_of_barcodes": 0.0}, {**ARC, "share_of_barcodes": 0.0}],
        "problems": [],
        "passed": True,
    }
    (tmp_path / "qc_summary.json").write_text(json.dumps({**base, **summary}))
    subprocess.run([sys.executable, str(SCRIPT), str(tmp_path)], check=True)
    return (tmp_path / "README.md").read_text()


def test_a_guessed_run_says_cell_ranger_detects_the_chemistry(tmp_path):
    text = readme(tmp_path, chemistry_guess={**V3, "share_of_barcodes": 0.97})
    assert "**3' v3/v3.1** (`SC3Pv3-polyA`): 97%" in text
    assert "Cell Ranger runs with `--chemistry auto`" in text
    assert "names the chemistry" not in text


def test_a_named_run_says_which_chemistry_cell_ranger_was_given(tmp_path):
    text = readme(tmp_path, chemistry_named="ARC-v1", chemistry_guess={**ARC, "share_of_barcodes": 0.95})
    assert "The run names the chemistry `ARC-v1`" in text
    assert "Cell Ranger runs with `--chemistry=ARC-v1`." in text
    assert "--chemistry auto" not in text


def test_a_named_run_off_its_list_points_to_the_problem_not_to_no_list(tmp_path):
    problem = "the run names ARC-v1 (Multiome GEX), but only 0% of barcodes are on its list"
    text = readme(tmp_path, chemistry_named="ARC-v1", problems=[problem], passed=False)
    assert "Fewer than half of the sampled R1 barcodes are on that list; see Problems." in text
    assert "No guess" not in text
    assert f"- {problem}" in text


def test_a_summary_from_before_named_chemistries_still_reads(tmp_path):
    summary = {"chemistry_guess": {**V3, "share_of_barcodes": 0.9}}
    base_text = readme(tmp_path, **summary)
    (tmp_path / "qc_summary.json").write_text(
        json.dumps({k: v for k, v in json.loads((tmp_path / "qc_summary.json").read_text()).items() if k != "chemistry_named"})
    )
    subprocess.run([sys.executable, str(SCRIPT), str(tmp_path)], check=True)
    assert (tmp_path / "README.md").read_text() == base_text
