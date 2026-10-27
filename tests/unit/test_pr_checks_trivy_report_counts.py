"""The Critical/High counts in pr-checks.yml's two Trivy report comments.

Trivy's table format prints a severity once per run of same-severity rows and
leaves the cell blank below it, so counting lines that contain "HIGH" counts
runs, not findings. These tests run the real report steps against a table with
merged cells and check the counts match Trivy's own Total lines.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).parent.parent.parent
PR_CHECKS = REPO_ROOT / ".github" / "workflows" / "pr-checks.yml"

_GIT_BASH = "/opt/homebrew/bin/bash"
BASH = _GIT_BASH if Path(_GIT_BASH).exists() else shutil.which("bash") or "bash"

# Two targets; the severity cell is merged across rows, as Trivy prints it.
MERGED_CELLS_SCAN = """\
bloom-web:ci (alpine 3.23.3)
============================
Total: 7 (HIGH: 4, CRITICAL: 3)

┌────────────┬────────────────┬──────────┬────────┐
│  Library   │ Vulnerability  │ Severity │ Status │
├────────────┼────────────────┼──────────┼────────┤
│ libcrypto3 │ CVE-2026-10001 │ CRITICAL │ fixed  │
│            │ CVE-2026-10002 │          │        │
│ libssl3    │ CVE-2026-10003 │          │        │
│            ├────────────────┼──────────┤        │
│            │ CVE-2026-10004 │ HIGH     │        │
│            │ CVE-2026-10005 │          │        │
│ musl       │ CVE-2026-10006 │          │        │
│ zlib       │ CVE-2026-10007 │          │        │
└────────────┴────────────────┴──────────┴────────┘

app/node_modules (node-pkg)
===========================
Total: 2 (HIGH: 2, CRITICAL: 0)

│ tar        │ CVE-2026-20001 │ HIGH     │ fixed  │
│            │ CVE-2026-20002 │          │        │
"""

CLEAN_SCAN = """\
caddy:ci (alpine 3.23.3)
========================
Total: 0 (HIGH: 0, CRITICAL: 0)
"""


def _job(name: str) -> dict:
    return yaml.safe_load(PR_CHECKS.read_text(encoding="utf-8"))["jobs"][name]


def _step(job: dict, name: str) -> dict:
    for step in job["steps"]:
        if step.get("name") == name:
            return step
    raise AssertionError(f"no step named {name!r}")


def _run(script: str, cwd: Path) -> None:
    assert "${{" not in script
    subprocess.run([BASH, "-e", "-c", script], cwd=cwd, check=True)


def test_built_images_report_counts_every_finding(tmp_path: Path) -> None:
    (tmp_path / "trivy-bloom-web.txt").write_text(MERGED_CELLS_SCAN, encoding="utf-8")
    (tmp_path / "trivy-caddy.txt").write_text(CLEAN_SCAN, encoding="utf-8")
    _run(_step(_job("docker-build"), "Generate Trivy report")["run"], tmp_path)

    report = (tmp_path / "trivy-report.md").read_text(encoding="utf-8").splitlines()
    assert "| bloom-web | 3 | 6 |" in report
    assert "| caddy | 0 | 0 |" in report


def test_pinned_summary_counts_every_finding(tmp_path: Path) -> None:
    (tmp_path / "trivy-result.txt").write_text(MERGED_CELLS_SCAN, encoding="utf-8")
    script = _step(_job("scan-pinned-images"), "Generate summary")["run"]
    _run(script.replace("${{ matrix.image }}", "ghcr.io/example/img:1"), tmp_path)

    fields = (tmp_path / "summary.txt").read_text(encoding="utf-8").split("|")
    assert fields[:3] == ["ghcr.io/example/img:1", "3", "6"]


def test_pinned_summary_counts_a_clean_scan_as_zero(tmp_path: Path) -> None:
    (tmp_path / "trivy-result.txt").write_text(CLEAN_SCAN, encoding="utf-8")
    script = _step(_job("scan-pinned-images"), "Generate summary")["run"]
    _run(script.replace("${{ matrix.image }}", "img"), tmp_path)

    fields = (tmp_path / "summary.txt").read_text(encoding="utf-8").split("|")
    assert fields[:3] == ["img", "0", "0"]


def _report_only_scans() -> list[dict]:
    built = [
        s
        for s in _job("docker-build")["steps"]
        if "trivy-action" in str(s.get("uses", "")) and str(s["with"].get("exit-code")) == "0"
    ]
    return built + [_step(_job("scan-pinned-images"), "Scan image")]


def test_report_only_scans_count_vulnerabilities_only() -> None:
    # Secret findings print their own Total lines, which would inflate the counts.
    scans = _report_only_scans()
    assert len(scans) == 7
    for scan in scans:
        assert scan["with"].get("scanners") == "vuln", scan.get("name")
