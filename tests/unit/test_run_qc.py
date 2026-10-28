"""Tests for argo/scrna/fastq_qc/run-qc.sh with stand-in fastq-qc, fastqc and qc-report on PATH."""

import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "argo/scrna/fastq_qc/run-qc.sh"

# fastq-qc records its arguments; the other two only succeed.
STUBS = {
    "fastq-qc": '#!/usr/bin/env bash\nprintf "%s\\n" "$@" > "${QC_ARGS}"\n',
    "fastqc": "#!/usr/bin/env bash\nexit 0\n",
    "qc-report": "#!/usr/bin/env bash\nexit 0\n",
}


@pytest.fixture
def env(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name, body in STUBS.items():
        path = bin_dir / name
        path.write_text(body)
        path.chmod(path.stat().st_mode | stat.S_IEXEC)
    (tmp_path / "work/fastq/root_tip").mkdir(parents=True)
    return {
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "QC_ARGS": str(tmp_path / "qc_args"),
        "SAMPLE": "root_tip",
        "CORES": "2",
        "WORK_DIR": str(tmp_path / "work"),
        "CELLRANGER_HOME": str(tmp_path),
    }


def qc_args(env, tmp_path) -> list[str]:
    result = subprocess.run([shutil.which("bash"), str(SCRIPT)], capture_output=True, text=True, env=env, check=False)
    assert result.returncode == 0, result.stdout + result.stderr
    return (tmp_path / "qc_args").read_text().splitlines()


def test_the_runs_chemistry_is_checked_against_the_reads(env, tmp_path):
    env["CHEMISTRY"] = "ARC-v1"
    args = qc_args(env, tmp_path)
    assert args[args.index("--expect-chemistry") + 1] == "ARC-v1"


def test_without_a_chemistry_the_check_guesses(env, tmp_path):
    args = qc_args(env, tmp_path)
    assert args[args.index("--expect-chemistry") + 1] == ""
