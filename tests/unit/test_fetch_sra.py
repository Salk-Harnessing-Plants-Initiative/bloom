"""Tests for argo/scrna/cellranger/fetch-sra.sh, which downloads a sample's SRA runs into
raw_reads/<sample>/ as Illumina-named FASTQs.

prefetch, fasterq-dump, pigz and aws are replaced by small stand-ins on PATH: the fake
fasterq-dump writes reads of the lengths given for each accession, and the fake aws keeps
"S3" in a local folder. The real fastq-sample-prefix checks the result.
"""

from __future__ import annotations

import gzip
import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

CELLRANGER = Path(__file__).resolve().parents[2] / "argo/scrna/cellranger"
SCRIPT = CELLRANGER / "fetch-sra.sh"


def _bash() -> str | None:
    bash = shutil.which("bash")
    if bash is None:
        return None
    version = subprocess.run(
        [bash, "-c", "echo ${BASH_VERSINFO[0]}"], capture_output=True, text=True, check=True
    ).stdout.strip()
    return bash if int(version) >= 4 else None


BASH = _bash()
pytestmark = pytest.mark.skipif(BASH is None, reason="needs bash 4+ (the pipeline image has it)")

STUBS = {
    "prefetch": """#!/usr/bin/env bash
acc="$1"; shift
while [ $# -gt 0 ]; do [ "$1" = --output-directory ] && out="$2"; shift; done
case " ${FAKE_MISSING:-} " in *" ${acc} "*) echo "not found" >&2; exit 3 ;; esac
mkdir -p "${out}/${acc}" && echo "sra" > "${out}/${acc}/${acc}.sra"
""",
    "fasterq-dump": """#!/usr/bin/env bash
src="$1"; acc="${src##*/}"; shift
while [ $# -gt 0 ]; do [ "$1" = --outdir ] && out="$2"; shift; done
mkdir -p "${out}"
var="FAKE_LAYOUT_${acc}"; layout="${!var}"
i=0
for len in ${layout//,/ }; do
  i=$((i + 1))
  seq=$(printf 'A%.0s' $(seq 1 "${len}"))
  for n in $(seq 1 20); do printf '@%s.%s\\n%s\\n+\\n%s\\n' "$acc" "$n" "$seq" "$seq"; done > "${out}/${acc}_${i}.fastq"
done
""",
    "pigz": """#!/usr/bin/env bash
for last; do :; done
gzip -c "${last}"
""",
    "aws": """#!/usr/bin/env bash
# aws s3 ls|cp against ${FAKE_S3}, standing in for s3://
[ "$1" = s3 ] || exit 2
cmd="$2"; shift 2
args=(); for a in "$@"; do [ "$a" = --only-show-errors ] || args+=("$a"); done
local_path() { echo "${FAKE_S3}/${1#s3://}"; }
case "$cmd" in
  ls)
    dir="$(local_path "${args[0]}")"
    [ -d "$dir" ] || exit 1
    for f in "$dir"/* "$dir"/.[!.]*; do [ -f "$f" ] && echo "2026-09-30 00:00:00 $(wc -c < "$f" | tr -d ' ') ${f##*/}"; done
    ;;
  cp)
    src="${args[0]}"; dst="${args[1]}"
    if [ "$src" = - ]; then d="$(local_path "$dst")"; mkdir -p "$(dirname "$d")"; cat > "$d"
    elif [ "$dst" = - ]; then cat "$(local_path "$src")"
    else d="$(local_path "$dst")"; mkdir -p "$(dirname "$d")"; cp "$src" "$d"; echo "$d" >> "${FAKE_S3}/.uploads"; fi
    ;;
esac
""",
}


@pytest.fixture
def env(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name, body in STUBS.items():
        path = bin_dir / name
        path.write_text(body)
        path.chmod(path.stat().st_mode | stat.S_IEXEC)
    (bin_dir / "fastq-sample-prefix").symlink_to(CELLRANGER / "fastq-sample-prefix.sh")
    s3 = tmp_path / "s3"
    s3.mkdir()
    return {
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "FAKE_S3": str(s3),
        "BUCKET": "bucket",
        "WORK_DIR": str(tmp_path / "work"),
        "OUTPUT_DIR": str(tmp_path / "outputs"),
        "THREADS": "2",
    }


def fetch(env, sample, runs, **layouts):
    run_env = {**env, "SAMPLE": sample, "SRA_RUNS": runs}
    run_env.update({f"FAKE_LAYOUT_{acc}": layout for acc, layout in layouts.items()})
    return subprocess.run(
        [BASH, str(SCRIPT)], capture_output=True, text=True, env=run_env, check=False
    )


def folder(env, sample):
    path = Path(env["FAKE_S3"]) / "bucket/raw_reads" / sample
    return sorted(p.name for p in path.iterdir()) if path.exists() else []


def read_length(env, sample, name):
    path = Path(env["FAKE_S3"]) / "bucket/raw_reads" / sample / name
    with gzip.open(path, "rt") as f:
        f.readline()
        return len(f.readline().strip())


def outputs(env):
    out = Path(env["OUTPUT_DIR"])
    return int((out / "fastq_count").read_text()), int((out / "total_bytes").read_text())


# --------------------------------------------------------------------------- #
# Naming the reads
# --------------------------------------------------------------------------- #


def test_a_10x_run_with_a_technical_barcode_read_is_named_the_illumina_way(env):
    result = fetch(env, "shahan_sc_1", "SRR12046049", SRR12046049="28,91,8")
    assert result.returncode == 0, result.stderr
    assert folder(env, "shahan_sc_1") == [
        ".sra-runs",
        "shahan_sc_1_S1_L001_I1_001.fastq.gz",
        "shahan_sc_1_S1_L001_R1_001.fastq.gz",
        "shahan_sc_1_S1_L001_R2_001.fastq.gz",
    ]
    assert read_length(env, "shahan_sc_1", "shahan_sc_1_S1_L001_R1_001.fastq.gz") == 28
    assert read_length(env, "shahan_sc_1", "shahan_sc_1_S1_L001_R2_001.fastq.gz") == 91
    assert read_length(env, "shahan_sc_1", "shahan_sc_1_S1_L001_I1_001.fastq.gz") == 8


def test_reads_are_assigned_by_length_whatever_order_sra_wrote_them(env):
    result = fetch(env, "s", "SRR1000001", SRR1000001="98,8,26")
    assert result.returncode == 0, result.stderr
    assert read_length(env, "s", "s_S1_L001_R1_001.fastq.gz") == 26
    assert read_length(env, "s", "s_S1_L001_R2_001.fastq.gz") == 98
    assert read_length(env, "s", "s_S1_L001_I1_001.fastq.gz") == 8


def test_two_index_reads_become_i1_and_i2(env):
    assert fetch(env, "s", "SRR1000001", SRR1000001="28,10,10,90").returncode == 0
    assert "s_S1_L001_I2_001.fastq.gz" in folder(env, "s")


def test_each_run_becomes_a_lane_in_the_order_given(env):
    result = fetch(env, "s", "SRR1000002, SRR1000001", SRR1000001="28,91", SRR1000002="26,98")
    assert result.returncode == 0, result.stderr
    assert read_length(env, "s", "s_S1_L001_R2_001.fastq.gz") == 98  # SRR1000002
    assert read_length(env, "s", "s_S1_L002_R2_001.fastq.gz") == 91  # SRR1000001


def test_the_counts_and_marker_are_written_last(env):
    fetch(env, "s", "SRR1000001 SRR1000002", SRR1000001="28,91", SRR1000002="28,91")
    count, total = outputs(env)
    assert count == 4
    assert total > 0
    marker = Path(env["FAKE_S3"]) / "bucket/raw_reads/s/.sra-runs"
    assert marker.read_text() == "SRR1000001\nSRR1000002\n"
    uploads = (Path(env["FAKE_S3"]) / ".uploads").read_text().split()
    assert all(u.endswith(".fastq.gz") for u in uploads)


# --------------------------------------------------------------------------- #
# Unusable reads
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "layout, message",
    [
        ("91", "don't include the 10x barcode read"),
        ("28", "don't include the 10x barcode read"),
        ("150,150", "two long reads"),
        ("28,28,91", "two 28-bp reads"),
        ("28,91,18", "18-bp read"),
        ("28,91,8,8,8", "more than two index reads"),
    ],
    ids=["cdna-only", "barcode-only", "bulk-paired", "two-barcode", "odd-length", "three-index"],
)
def test_reads_that_arent_10x_are_refused_and_nothing_uploaded(env, layout, message):
    result = fetch(env, "s", "SRR1000001", SRR1000001=layout)
    assert result.returncode == 11
    assert message in result.stderr
    assert folder(env, "s") == []


def test_a_run_that_cant_be_downloaded_fails_with_10(env):
    env["FAKE_MISSING"] = "SRR1000002"
    result = fetch(env, "s", "SRR1000001,SRR1000002", SRR1000001="28,91")
    assert result.returncode == 10
    assert "couldn't download SRR1000002" in result.stderr
    assert folder(env, "s") == []


# --------------------------------------------------------------------------- #
# Existing folders
# --------------------------------------------------------------------------- #


def test_a_folder_already_holding_these_runs_is_left_as_it_is(env):
    assert fetch(env, "s", "SRR1000001", SRR1000001="28,91").returncode == 0
    uploads = Path(env["FAKE_S3"]) / ".uploads"
    uploads.unlink()
    result = fetch(env, "s", "SRR1000001", SRR1000001="28,91")
    assert result.returncode == 0, result.stderr
    assert "Already downloaded" in result.stdout
    assert not uploads.exists()
    assert outputs(env)[0] == 2


def test_a_folder_holding_other_runs_is_refused(env):
    assert fetch(env, "s", "SRR1000001", SRR1000001="28,91").returncode == 0
    result = fetch(env, "s", "SRR1000009", SRR1000009="28,91")
    assert result.returncode == 12
    assert "other SRA runs" in result.stderr


def test_a_folder_holding_other_fastqs_is_refused_untouched(env):
    manual = Path(env["FAKE_S3"]) / "bucket/raw_reads/s"
    manual.mkdir(parents=True)
    (manual / "L007-259_S1_L001_R1_001.fastq.gz").write_text("x")
    result = fetch(env, "s", "SRR1000001", SRR1000001="28,91")
    assert result.returncode == 12
    assert folder(env, "s") == ["L007-259_S1_L001_R1_001.fastq.gz"]


# --------------------------------------------------------------------------- #
# Inputs
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "sample, runs",
    [
        ("s", "GSE152766"),
        ("s", "SRR12"),
        ("s", "SRR1000001,SRR1000001"),
        ("s", ",".join(f"SRR100000{i}" for i in range(10))),
        ("s", " "),
        ("S1.rep1", "SRR1000001"),
        ("a__b", "SRR1000001"),
    ],
    ids=["study", "too-short", "twice", "ten-runs", "empty", "dotted-name", "double-underscore"],
)
def test_bad_inputs_are_refused_before_any_download(env, sample, runs):
    result = fetch(env, sample, runs)
    assert result.returncode == 6
    assert not Path(env["WORK_DIR"]).exists()


def test_err_and_drr_accessions_are_accepted(env):
    result = fetch(env, "s", "ERR1000001 DRR1000002", ERR1000001="28,91", DRR1000002="28,91")
    assert result.returncode == 0, result.stderr


def test_the_image_installs_it():
    dockerfile = (CELLRANGER.parent / "Dockerfile").read_text()
    assert "COPY cellranger/fetch-sra.sh /usr/local/bin/fetch-sra" in dockerfile
    assert "/usr/local/bin/fetch-sra" in dockerfile.split("RUN chmod +x", 1)[1].split("\n")[0]
    assert "fasterq-dump" in dockerfile and "pigz" in dockerfile
