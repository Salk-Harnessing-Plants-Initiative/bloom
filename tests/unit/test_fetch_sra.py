"""Tests for argo/scrna/cellranger/fetch-sra.sh, which downloads a sample's SRA runs into
raw_reads/<sample>/ as Illumina-named FASTQs.

prefetch, fasterq-dump, pigz and aws are replaced by small stand-ins on PATH: the fake
fasterq-dump writes reads of the lengths given for each accession (dropping the technical
ones, 28 bp or shorter, unless --include-technical is passed), and the fake aws keeps "S3" in
a local folder and logs every write in order. The real fastq-sample-prefix checks the result.
"""

from __future__ import annotations

import gzip
import os
import re
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
tech=0
while [ $# -gt 0 ]; do
  [ "$1" = --outdir ] && out="$2"
  [ "$1" = --include-technical ] && tech=1
  shift
done
case " ${FAKE_FASTERQ_FAIL:-} " in *" ${acc} "*) echo "fasterq-dump failed" >&2; exit 3 ;; esac
mkdir -p "${out}"
var="FAKE_LAYOUT_${acc}"; layout="${!var}"
bvar="FAKE_BARCODED_${acc}"; barcoded="${!bvar:-}"
i=0
for len in ${layout//,/ }; do
  i=$((i + 1))
  [ "${tech}" = 1 ] || [ "${len}" -gt 28 ] || continue
  if [ "${i}" = "${barcoded}" ]; then
    seq="AAACCCAAGAAACACT$(printf 'A%.0s' $(seq 1 $((len - 16))))"
  else
    seq=$(printf 'C%.0s' $(seq 1 "${len}"))
  fi
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
    [ -z "${FAKE_LS_FAIL:-}" ] || { echo "An error occurred (AccessDenied) when calling ListObjectsV2" >&2; exit 255; }
    dir="$(local_path "${args[0]}")"
    [ -d "$dir" ] || exit 1
    for f in "$dir"/* "$dir"/.[!.]*; do [ -f "$f" ] && echo "2026-09-30 00:00:00 $(wc -c < "$f" | tr -d ' ') ${f##*/}"; done
    exit 0
    ;;
  cp)
    src="${args[0]}"; dst="${args[1]}"
    if [ "$dst" = - ]; then
      [ -z "${FAKE_MARKER_FAIL:-}" ] || { echo "An error occurred (500) when calling GetObject" >&2; exit 255; }
      cat "$(local_path "$src")"; exit 0
    fi
    case "$dst" in *${FAKE_CP_FAIL_ON:-/nothing/}*) echo "upload failed: $dst" >&2; exit 1 ;; esac
    d="$(local_path "$dst")"; mkdir -p "$(dirname "$d")"
    if [ "$src" = - ]; then cat > "$d"; else cp "$src" "$d"; fi
    echo "$d" >> "${FAKE_S3}/.uploads"
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
    barcodes = tmp_path / "barcodes"
    barcodes.mkdir()
    (barcodes / "737K-august-2016.txt").write_text("AAACCCAAGAAACACT\nAAACCCAAGAAACCAT\n")
    return {
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "FAKE_S3": str(s3),
        "BUCKET": "bucket",
        "WORK_DIR": str(tmp_path / "work"),
        "OUTPUT_DIR": str(tmp_path / "outputs"),
        "THREADS": "2",
        "BARCODE_DIR": str(barcodes),
    }


def fetch(env, sample, runs, barcoded=None, **layouts):
    """barcoded: {accession: position of the read that carries 10x barcodes}."""
    run_env = {**env, "SAMPLE": sample, "SRA_RUNS": runs}
    run_env.update({f"FAKE_LAYOUT_{acc}": layout for acc, layout in layouts.items()})
    run_env.update({f"FAKE_BARCODED_{acc}": str(i) for acc, i in (barcoded or {}).items()})
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


def uploads(env):
    path = Path(env["FAKE_S3"]) / ".uploads"
    return [Path(p).name for p in path.read_text().split()] if path.exists() else []


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
    written = uploads(env)
    assert written[-1] == ".sra-runs"
    assert len(written) == 5 and all(u.endswith(".fastq.gz") for u in written[:-1])


# --------------------------------------------------------------------------- #
# Unusable reads
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "layout, message",
    [
        ("91", "don't include both the 10x barcode read"),
        ("28", "don't include both the 10x barcode read"),
        ("150,150", "can't tell which is the 10x barcode read"),
        ("28,28", "two 28-bp reads and no cDNA read"),
        ("28,28,91", "3 reads longer than an index read"),
        ("28,91,18", "18-bp read"),
        ("28,91,8,8,8", "more than two index reads"),
    ],
    ids=["cdna-only", "barcode-only", "bulk-paired", "no-cdna", "three-long", "odd-length", "three-index"],
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
    (Path(env["FAKE_S3"]) / ".uploads").unlink()
    shutil.rmtree(env["OUTPUT_DIR"])
    result = fetch(env, "s", "SRR1000001", SRR1000001="28,91")
    assert result.returncode == 0, result.stderr
    assert "Already downloaded" in result.stdout
    assert uploads(env) == []
    count, total = outputs(env)
    assert count == 2 and total > 0


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


def test_the_image_installs_it_and_the_template_runs_it():
    dockerfile = (CELLRANGER.parent / "Dockerfile").read_text()
    assert "COPY cellranger/fetch-sra.sh /usr/local/bin/fetch-sra" in dockerfile
    assert "/usr/local/bin/fetch-sra" in dockerfile.split("RUN chmod +x", 1)[1].split("\n")[0]
    assert "for tool in prefetch fasterq-dump vdb-config; do ln -s" in dockerfile
    assert "apt-get install -y --no-install-recommends pigz" in dockerfile
    template = (CELLRANGER / "cellranger-count-template.yaml").read_text()
    step = template.split("- name: fetch-sra\n", 1)[1].split("\n    - name: ", 1)[0]
    assert 'command: [run-with-log, --path, "scrna/{{workflow.name}}/fetch-sra.log", --, fetch-sra]' in step
    tags = set(re.findall(r"cellranger:(\S+)", template))
    assert len(tags) == 1, f"the template uses more than one image: {tags}"


# --------------------------------------------------------------------------- #
# Untrimmed R1
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "layout, barcoded, r1, r2",
    [
        ("8,150,150", 2, 150, 150),
        ("150,150,10,10", 1, 150, 150),
        ("30,91", 1, 30, 91),
        ("8,150,91", 2, 150, 91),
    ],
    ids=["2x150-barcode-second", "2x150-barcode-first", "30bp-r1", "r1-longer-than-r2"],
)
def test_an_untrimmed_r1_is_found_by_its_barcodes(env, layout, barcoded, r1, r2):
    result = fetch(env, "s", "SRR1000001", barcoded={"SRR1000001": barcoded}, SRR1000001=layout)
    assert result.returncode == 0, result.stderr
    assert read_length(env, "s", "s_S1_L001_R1_001.fastq.gz") == r1
    assert read_length(env, "s", "s_S1_L001_R2_001.fastq.gz") == r2
    with gzip.open(Path(env["FAKE_S3"]) / "bucket/raw_reads/s/s_S1_L001_R1_001.fastq.gz", "rt") as f:
        f.readline()
        assert f.readline().startswith("AAACCCAAGAAACACT")


def test_a_trimmed_r1_needs_no_barcode_check(env):
    result = fetch(env, "s", "SRR1000001", SRR1000001="28,91,8")
    assert result.returncode == 0, result.stderr
    assert "barcode list" not in result.stdout


def test_two_long_reads_both_with_barcodes_are_refused(env, tmp_path):
    # Both reads carry list barcodes: the fake writes the same barcode into each when asked twice.
    run_env = {**env, "SAMPLE": "s", "SRA_RUNS": "SRR1000001", "FAKE_LAYOUT_SRR1000001": "150,150"}
    list_file = Path(env["BARCODE_DIR"]) / "737K-august-2016.txt"
    list_file.write_text(list_file.read_text() + "CCCCCCCCCCCCCCCC\n")
    result = subprocess.run([BASH, str(SCRIPT)], capture_output=True, text=True, env=run_env, check=False)
    assert result.returncode == 11
    assert "can't tell which is the 10x barcode read" in result.stderr


def test_a_20bp_long_read_is_refused(env):
    result = fetch(env, "s", "SRR1000001", SRR1000001="20,91")
    assert result.returncode == 11
    assert "20-bp read" in result.stderr


def test_without_include_technical_the_barcode_read_would_be_lost(env, tmp_path):
    patched = tmp_path / "no-technical.sh"
    patched.write_text(SCRIPT.read_text().replace(" --include-technical", ""))
    run_env = {**env, "SAMPLE": "s", "SRA_RUNS": "SRR1000001", "FAKE_LAYOUT_SRR1000001": "28,91,8"}
    result = subprocess.run([BASH, str(patched)], capture_output=True, text=True, env=run_env, check=False)
    assert result.returncode == 11


# --------------------------------------------------------------------------- #
# S3 that can't be checked
# --------------------------------------------------------------------------- #


def test_a_folder_that_cant_be_listed_is_left_untouched(env):
    assert fetch(env, "s", "SRR1000001", SRR1000001="28,91").returncode == 0
    before = {p.name: p.read_bytes() for p in (Path(env["FAKE_S3"]) / "bucket/raw_reads/s").iterdir()}
    (Path(env["FAKE_S3"]) / ".uploads").unlink()
    env["FAKE_LS_FAIL"] = "1"
    result = fetch(env, "s", "SRR1000009", SRR1000009="26,98")
    assert result.returncode == 10
    assert "couldn't check" in result.stderr and "AccessDenied" in result.stderr
    assert uploads(env) == []
    after = {p.name: p.read_bytes() for p in (Path(env["FAKE_S3"]) / "bucket/raw_reads/s").iterdir()}
    assert after == before


def test_an_empty_folder_is_told_apart_from_one_that_cant_be_listed(env):
    env["FAKE_LS_FAIL"] = "1"
    assert fetch(env, "fresh", "SRR1000001", SRR1000001="28,91").returncode == 10
    del env["FAKE_LS_FAIL"]
    assert fetch(env, "fresh", "SRR1000001", SRR1000001="28,91").returncode == 0


def test_a_marker_that_cant_be_read_fails_with_10_not_12(env):
    assert fetch(env, "s", "SRR1000001", SRR1000001="28,91").returncode == 0
    env["FAKE_MARKER_FAIL"] = "1"
    result = fetch(env, "s", "SRR1000001", SRR1000001="28,91")
    assert result.returncode == 10
    assert "couldn't read" in result.stderr


def test_an_upload_failure_writes_no_marker(env):
    env["FAKE_CP_FAIL_ON"] = "_R2_001.fastq.gz"
    result = fetch(env, "s", "SRR1000001", SRR1000001="28,91,8")
    assert result.returncode != 0
    assert ".sra-runs" not in folder(env, "s")


def test_a_failed_conversion_fails_with_10_and_uploads_nothing(env):
    env["FAKE_FASTERQ_FAIL"] = "SRR1000001"
    result = fetch(env, "s", "SRR1000001", SRR1000001="28,91")
    assert result.returncode == 10
    assert "couldn't convert SRR1000001" in result.stderr
    assert folder(env, "s") == []


# --------------------------------------------------------------------------- #
# Accession lists
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "runs",
    ["SRR1000001\nSRR1000002", "SRR1000001\r\nSRR1000002\n", "SRR1000001,\tSRR1000002", " SRR1000001 , SRR1000002 "],
    ids=["newline", "windows-newline", "comma-tab", "padded"],
)
def test_pasted_lists_keep_every_run(env, runs):
    result = fetch(env, "s", runs, SRR1000001="28,91", SRR1000002="28,91")
    assert result.returncode == 0, result.stderr
    assert "s_S1_L002_R2_001.fastq.gz" in folder(env, "s")
    marker = Path(env["FAKE_S3"]) / "bucket/raw_reads/s/.sra-runs"
    assert marker.read_text() == "SRR1000001\nSRR1000002\n"


def test_nine_runs_make_lanes_1_to_9(env):
    runs = [f"SRR100000{i}" for i in range(1, 10)]
    result = fetch(env, "s", ",".join(runs), **{acc: "28,91" for acc in runs})
    assert result.returncode == 0, result.stderr
    assert "s_S1_L009_R2_001.fastq.gz" in folder(env, "s")


@pytest.mark.parametrize("missing", ["SAMPLE", "SRA_RUNS"])
def test_a_missing_sample_or_run_list_is_refused_with_6(env, missing):
    run_env = {**env, "SAMPLE": "s", "SRA_RUNS": "SRR1000001", "FAKE_LAYOUT_SRR1000001": "28,91"}
    run_env[missing] = ""
    result = subprocess.run([BASH, str(SCRIPT)], capture_output=True, text=True, env=run_env, check=False)
    assert result.returncode == 6


# --------------------------------------------------------------------------- #
# Read-length edges and leftovers
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "layout, name",
    [("28,91,6", "s_S1_L001_I1_001.fastq.gz"), ("26,50", "s_S1_L001_R2_001.fastq.gz")],
    ids=["6bp-index", "50bp-cdna"],
)
def test_the_length_edges_are_accepted(env, layout, name):
    result = fetch(env, "s", "SRR1000001", SRR1000001=layout)
    assert result.returncode == 0, result.stderr
    assert name in folder(env, "s")


def test_files_left_in_the_work_folder_are_not_uploaded(env):
    stale = Path(env["WORK_DIR"]) / "out"
    stale.mkdir(parents=True)
    (stale / "s_S1_L009_R1_001.fastq.gz").write_bytes(gzip.compress(b"@x\nA\n+\nA\n"))
    assert fetch(env, "s", "SRR1000001", SRR1000001="28,91").returncode == 0
    assert "s_S1_L009_R1_001.fastq.gz" not in folder(env, "s")


def test_a_name_check_failure_fails_with_7_and_uploads_nothing(env):
    bin_dir = Path(env["PATH"].split(":", 1)[0])
    (bin_dir / "fastq-sample-prefix").unlink()
    (bin_dir / "fastq-sample-prefix").write_text("#!/usr/bin/env bash\necho misnamed >&2\nexit 7\n")
    (bin_dir / "fastq-sample-prefix").chmod(0o755)
    result = fetch(env, "s", "SRR1000001", SRR1000001="28,91")
    assert result.returncode == 7
    assert folder(env, "s") == []
