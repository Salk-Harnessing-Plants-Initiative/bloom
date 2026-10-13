"""Tests for argo/scrna/cellranger/fetch-sra.sh, which downloads a sample's SRA runs into the
run's folder (fastq/<sample>/) as Illumina-named .fastq.gz files: ENA's files when usable,
otherwise converted from SRA through fastq-dump, compressed as it streams.

curl (ENA's file report and files), prefetch, fastq-dump and pigz are replaced by small stand-ins
on PATH, which log each call. A run's reads are given as a layout of read lengths, e.g. "28,91,8";
ENA serves a run only when the test publishes it. The real claim-run-dir and fastq-sample-prefix
are used. There is no aws on PATH, so any S3 call would fail the step.
"""

from __future__ import annotations

import gzip
import hashlib
import os
import re
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

CELLRANGER = Path(__file__).resolve().parents[2] / "argo/scrna/cellranger"
SCRIPT = CELLRANGER / "fetch-sra.sh"
BARCODE = "AAACCCAAGAAACACT"


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


def _records(acc: str, layout: str, barcoded: int | None, read: int, header) -> str:
    """20 reads of one read position; header(spot) is each record's first line."""
    length = int(layout.split(",")[read - 1])
    if read == barcoded:
        seq = BARCODE + "A" * (length - len(BARCODE))
    else:
        seq = "C" * length
    return "".join(f"{header(n)}\n{seq}\n+\n{seq}\n" for n in range(1, 21))


STUBS = {
    "prefetch": """#!/usr/bin/env bash
echo "prefetch $1" >> "${FAKE_LOG}"
acc="$1"; shift
while [ $# -gt 0 ]; do [ "$1" = --output-directory ] && out="$2"; shift; done
case " ${FAKE_MISSING:-} " in *" ${acc} "*) echo "not found" >&2; exit 3 ;; esac
mkdir -p "${out}/${acc}" && echo "sra" > "${out}/${acc}/${acc}.sra"
""",
    # Names each record from --defline-seq, as the real tool does; its default otherwise. The run's
    # records are stored with "<acc> <spot> <read> <length>" as their first line.
    "fastq-dump": """#!/usr/bin/env bash
fmt='@$ac.$si $sn length=$rl'
args=("$@")
for i in "${!args[@]}"; do [ "${args[$i]}" = --defline-seq ] && fmt="${args[$((i + 1))]}"; done
for last; do :; done
acc="${last##*/}"
echo "fastq-dump ${acc} $*" >> "${FAKE_LOG}"
case " ${FAKE_DUMP_FAIL:-} " in *" ${acc} "*) echo "fastq-dump failed" >&2; exit 3 ;; esac
awk -v fmt="${fmt}" 'NR % 4 == 1 {
    h = fmt
    gsub(/[$]ac/, $1, h); gsub(/[$]si/, $2, h); gsub(/[$]ri/, $3, h)
    gsub(/[$]sn/, $1 "." $2, h); gsub(/[$]rl/, $4, h)
    print h; next
  } { print }' "${FAKE_SRA}/${acc}.fastq"
""",
    "pigz": """#!/usr/bin/env bash
gzip -c
""",
    "curl": """#!/usr/bin/env bash
out=""; url=""
while [ $# -gt 0 ]; do
  case "$1" in -o) out="$2"; shift ;; --max-time|--retry) shift ;; -*) ;; *) url="$1" ;; esac
  shift
done
echo "curl ${url}" >> "${FAKE_LOG}"
[ -z "${FAKE_ENA_DOWN:-}" ] || { echo "curl: (6) Could not resolve host" >&2; exit 6; }
case "${url}" in
  https://ena.test/api*)
    acc="$(sed -E 's/.*accession=([A-Z0-9]+).*/\\1/' <<<"${url}")"
    printf 'run_accession\\tfastq_ftp\\tfastq_md5\\n'
    [ -f "${FAKE_ENA}/${acc}.row" ] && cat "${FAKE_ENA}/${acc}.row"
    exit 0 ;;
  https://ftp.ena.test/*)
    file="${FAKE_ENA}/${url##*/}"
    [ -f "${file}" ] || { echo "curl: (22) 404" >&2; exit 22; }
    if [ -n "${out}" ]; then
      case " ${FAKE_CORRUPT:-} " in *" ${url##*/} "*) echo junk > "${out}"; exit 0 ;; esac
      cp "${file}" "${out}"
    else
      cat "${file}"
    fi ;;
  *) echo "curl: unexpected ${url}" >&2; exit 2 ;;
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
    (bin_dir / "claim-run-dir").symlink_to(CELLRANGER / "claim-run-dir.sh")
    for name in ("sra", "ena", "barcodes"):
        (tmp_path / name).mkdir()
    (tmp_path / "barcodes/737K-august-2016.txt").write_text(f"{BARCODE}\nAAACCCAAGAAACCAT\n")
    return {
        # No aws: the step must not touch S3.
        "PATH": f"{bin_dir}:/usr/bin:/bin",
        "RUN_DIR": str(tmp_path / "runs/key"),
        "THREADS": "2",
        "BARCODE_DIR": str(tmp_path / "barcodes"),
        "ENA_API": "https://ena.test/api",
        "FAKE_LOG": str(tmp_path / "calls.log"),
        "FAKE_SRA": str(tmp_path / "sra"),
        "FAKE_ENA": str(tmp_path / "ena"),
    }


def on_sra(env, acc, layout, barcoded=None):
    """What fastq-dump --split-spot gives for the run: its reads interleaved per spot."""
    lengths = layout.split(",")
    reads = [_records(acc, layout, barcoded, r, lambda n, r=r: f"{acc} {n} {r} {lengths[r - 1]}")
             .splitlines(keepends=True) for r in range(1, len(lengths) + 1)]
    spots = ["".join(r[i:i + 4]) for i in range(0, len(reads[0]), 4) for r in reads]
    (Path(env["FAKE_SRA"]) / f"{acc}.fastq").write_text("".join(spots))


def on_ena(env, acc, layout, barcoded=None, reads=None):
    """ENA's .fastq.gz files for the run: all its reads, or just the read positions given."""
    urls, md5s = [], []
    for r in reads or range(1, len(layout.split(",")) + 1):
        name = f"{acc}_{r}.fastq.gz"
        # ENA names a spot's reads alike, e.g. "@SRR1.5 5/1" and "@SRR1.5 5/2".
        records = _records(acc, layout, barcoded, r, lambda n, r=r: f"@{acc}.{n} {n}/{r}")
        data = gzip.compress(records.encode())
        (Path(env["FAKE_ENA"]) / name).write_bytes(data)
        urls.append(f"ftp.ena.test/vol1/fastq/{acc}/{name}")
        md5s.append(hashlib.md5(data).hexdigest())
    (Path(env["FAKE_ENA"]) / f"{acc}.row").write_text(f"{acc}\t{';'.join(urls)}\t{';'.join(md5s)}\n")


def fetch(env, sample, runs):
    return subprocess.run(
        [BASH, str(SCRIPT)], capture_output=True, text=True, check=False,
        env={**env, "SAMPLE": sample, "SRA_RUNS": runs},
    )


def dest(env, sample="s"):
    return Path(env["RUN_DIR"]) / "fastq" / sample


def fastqs(env, sample="s"):
    return sorted(p.name for p in dest(env, sample).glob("*.fastq.gz"))


def calls(env):
    log = Path(env["FAKE_LOG"])
    return log.read_text().splitlines() if log.exists() else []


def read_names(path):
    """Each record's name: its first line up to the first space, as Cell Ranger compares them."""
    with gzip.open(path, "rt") as f:
        return [line.split()[0] for i, line in enumerate(f) if i % 4 == 0]


def first_read(path):
    with gzip.open(path, "rt") as f:
        return [f.readline() for _ in range(2)][1].strip()


# --------------------------------------------------------------------------- #
# From SRA
# --------------------------------------------------------------------------- #


def test_a_10x_run_is_converted_and_named_the_illumina_way(env):
    on_sra(env, "SRR1000001", "8,28,91")
    result = fetch(env, "s", "SRR1000001")
    assert result.returncode == 0, result.stderr
    assert fastqs(env) == ["s_S1_L001_I1_001.fastq.gz", "s_S1_L001_R1_001.fastq.gz",
                           "s_S1_L001_R2_001.fastq.gz"]
    assert len(first_read(dest(env) / "s_S1_L001_R2_001.fastq.gz")) == 91
    assert (dest(env) / ".sra-runs").read_text() == "SRR1000001\n"


def test_the_reads_are_streamed_through_compression_and_nothing_uncompressed_is_left(env):
    on_sra(env, "SRR1000001", "28,91")
    assert fetch(env, "s", "SRR1000001").returncode == 0
    dump = next(c for c in calls(env) if c.startswith("fastq-dump"))
    assert "--split-spot" in dump and "--stdout" in dump
    assert not list(Path(env["RUN_DIR"]).parent.rglob("*.fastq"))
    assert not (Path(env["RUN_DIR"]) / "sra").exists(), "the .sra is removed once converted"
    assert not list(dest(env).glob(".part-*"))


@pytest.mark.parametrize("source", [on_sra, on_ena], ids=["sra", "ena"])
def test_a_spots_reads_have_the_same_name_in_every_file(env, source):
    source(env, "SRR1000001", "8,28,91")
    assert fetch(env, "s", "SRR1000001").returncode == 0
    names = [read_names(dest(env) / f"s_S1_L001_{read}_001.fastq.gz") for read in ("I1", "R1", "R2")]
    assert names[0] == names[1] == names[2]
    assert names[1][:2] == ["@SRR1000001.1", "@SRR1000001.2"]


def test_sra_records_keep_their_read_number_out_of_the_name(env):
    on_sra(env, "SRR1000001", "28,91")
    assert fetch(env, "s", "SRR1000001").returncode == 0
    dump = next(c for c in calls(env) if c.startswith("fastq-dump"))
    assert "--defline-seq @$ac.$si $ri" in dump
    with gzip.open(dest(env) / "s_S1_L001_R2_001.fastq.gz", "rt") as f:
        assert f.readline() == "@SRR1000001.1\n"


def test_records_not_named_as_asked_fail_with_10_and_leave_no_fastq(env):
    # fastq-dump's own default names ("@acc.spot acc.spot length=n") can't be split by read.
    on_sra(env, "SRR1000001", "28,91")
    stub = Path(env["PATH"].split(":")[0]) / "fastq-dump"
    stub.write_text(stub.read_text().replace('&& fmt="${args[$((i + 1))]}"', "&& :"))
    result = fetch(env, "s", "SRR1000001")
    assert result.returncode == 10
    assert "bad record name" in result.stderr
    assert fastqs(env) == []


def test_reads_are_assigned_by_length_whatever_order_sra_wrote_them(env):
    on_sra(env, "SRR1000001", "91,8,28")
    assert fetch(env, "s", "SRR1000001").returncode == 0
    assert len(first_read(dest(env) / "s_S1_L001_R1_001.fastq.gz")) == 28


def test_two_index_reads_become_i1_and_i2(env):
    on_sra(env, "SRR1000001", "8,8,28,91")
    assert fetch(env, "s", "SRR1000001").returncode == 0
    assert "s_S1_L001_I2_001.fastq.gz" in fastqs(env)


@pytest.mark.parametrize("layout, barcoded, r1", [("150,150", 1, 1), ("150,150", 2, 2)])
def test_an_untrimmed_r1_is_found_by_its_barcodes(env, layout, barcoded, r1):
    on_sra(env, "SRR1000001", layout, barcoded)
    assert fetch(env, "s", "SRR1000001").returncode == 0
    assert first_read(dest(env) / "s_S1_L001_R1_001.fastq.gz").startswith(BARCODE)


@pytest.mark.parametrize(
    "layout, message",
    [("91", "don't include both"), ("26,28", "no cDNA read"), ("20,91", "20-bp read"),
     ("28,91,91", "3 reads longer"), ("8,8,8,28,91", "more than two index reads")],
)
def test_reads_that_arent_10x_fail_with_11_and_leave_no_fastq(env, layout, message):
    on_sra(env, "SRR1000001", layout)
    result = fetch(env, "s", "SRR1000001")
    assert result.returncode == 11
    assert message in result.stderr
    assert fastqs(env) == []


def test_two_long_reads_both_with_barcodes_are_refused(env, tmp_path):
    on_sra(env, "SRR1000001", "150,150", barcoded=1)
    path = Path(env["FAKE_SRA"]) / "SRR1000001.fastq"
    path.write_text(path.read_text().replace("C" * 150, BARCODE + "C" * 134))
    result = fetch(env, "s", "SRR1000001")
    assert result.returncode == 11 and "can't tell which" in result.stderr


@pytest.mark.parametrize("fail", ["FAKE_MISSING", "FAKE_DUMP_FAIL"])
def test_a_run_that_cant_be_downloaded_or_converted_fails_with_10(env, fail):
    on_sra(env, "SRR1000001", "28,91")
    env[fail] = "SRR1000001"
    result = fetch(env, "s", "SRR1000001")
    assert result.returncode == 10
    assert fastqs(env) == []


# --------------------------------------------------------------------------- #
# From ENA first
# --------------------------------------------------------------------------- #


def test_enas_files_are_used_when_they_hold_both_reads(env):
    on_ena(env, "SRR1000001", "28,91")
    result = fetch(env, "s", "SRR1000001")
    assert result.returncode == 0, result.stderr
    assert fastqs(env) == ["s_S1_L001_R1_001.fastq.gz", "s_S1_L001_R2_001.fastq.gz"]
    assert (dest(env) / "s_S1_L001_R2_001.fastq.gz").read_bytes() == (
        Path(env["FAKE_ENA"]) / "SRR1000001_2.fastq.gz").read_bytes(), "used as ENA serves it"
    assert not any(c.startswith(("prefetch", "fastq-dump")) for c in calls(env))


def test_enas_untrimmed_r1_is_found_by_its_barcodes(env):
    on_ena(env, "SRR1000001", "150,150", barcoded=2)
    assert fetch(env, "s", "SRR1000001").returncode == 0
    assert first_read(dest(env) / "s_S1_L001_R1_001.fastq.gz").startswith(BARCODE)


@pytest.mark.parametrize(
    "setup, said",
    [
        (lambda env: on_ena(env, "SRR1000001", "28,91", reads=[2]), "not a barcode and a cDNA"),
        (lambda env: on_ena(env, "SRR1000001", "91,91"), "aren't usable as they are"),
        (lambda env: env.update(FAKE_CORRUPT="SRR1000001_1.fastq.gz") or on_ena(
            env, "SRR1000001", "28,91"), "doesn't match ENA's MD5"),
        (lambda env: env.update(FAKE_ENA_DOWN="1"), "ENA couldn't be asked"),
        (lambda env: None, "ENA has no FASTQs"),
    ],
    ids=["one-file", "no-barcode-read", "md5-mismatch", "unreachable", "not-on-ena"],
)
def test_otherwise_the_run_is_converted_from_sra(env, setup, said):
    on_sra(env, "SRR1000001", "28,91")
    setup(env)
    result = fetch(env, "s", "SRR1000001")
    assert result.returncode == 0, result.stderr
    assert said in result.stdout
    assert "prefetch SRR1000001" in calls(env)
    assert len(first_read(dest(env) / "s_S1_L001_R2_001.fastq.gz")) == 91


# --------------------------------------------------------------------------- #
# Lanes, retries and the run's folder
# --------------------------------------------------------------------------- #


def test_each_run_becomes_a_lane_in_the_order_given_from_either_source(env):
    on_ena(env, "SRR1000002", "28,91")
    on_sra(env, "SRR1000001", "28,91")
    assert fetch(env, "s", "SRR1000002,SRR1000001").returncode == 0
    assert "s_S1_L002_R2_001.fastq.gz" in fastqs(env)
    assert (dest(env) / ".sra-runs").read_text() == "SRR1000002\nSRR1000001\n"


@pytest.mark.parametrize(
    "runs", ["SRR1000001\nSRR1000002", "SRR1000001\r\nSRR1000002\n", "SRR1000001,\tSRR1000002",
             " SRR1000001 , SRR1000002 "],
    ids=["newline", "windows-newline", "comma-tab", "padded"],
)
def test_pasted_lists_keep_every_run(env, runs):
    on_sra(env, "SRR1000001", "28,91")
    on_sra(env, "SRR1000002", "28,91")
    assert fetch(env, "s", runs).returncode == 0
    assert "s_S1_L002_R2_001.fastq.gz" in fastqs(env)


def test_a_retry_resumes_after_the_lanes_already_downloaded(env):
    on_sra(env, "SRR1000001", "28,91")
    on_sra(env, "SRR1000002", "28,91")
    env["FAKE_MISSING"] = "SRR1000002"
    assert fetch(env, "s", "SRR1000001,SRR1000002").returncode == 10
    assert not (dest(env) / ".sra-runs").exists()
    del env["FAKE_MISSING"]
    Path(env["FAKE_LOG"]).unlink()
    assert fetch(env, "s", "SRR1000001,SRR1000002").returncode == 0
    assert "prefetch SRR1000001" not in calls(env)
    assert len(fastqs(env)) == 4


def test_a_finished_download_is_left_as_it_is(env):
    on_sra(env, "SRR1000001", "28,91")
    assert fetch(env, "s", "SRR1000001").returncode == 0
    Path(env["FAKE_LOG"]).unlink()
    result = fetch(env, "s", "SRR1000001")
    assert result.returncode == 0 and "Already downloaded" in result.stdout
    assert calls(env) == []


def test_a_folder_left_by_a_run_on_other_reads_is_set_aside(env):
    run_dir = Path(env["RUN_DIR"])
    (run_dir / "fastq/s").mkdir(parents=True)
    (run_dir / "fastq/s/s_S1_L001_R1_001.fastq.gz").write_bytes(b"old")
    (run_dir / "logs").mkdir()
    (run_dir / ".inputs").write_text("sra SRR9999999\n")
    on_sra(env, "SRR1000001", "28,91")
    assert fetch(env, "s", "SRR1000001").returncode == 0
    assert (run_dir / ".inputs").read_text() == "sra SRR1000001\n"
    assert first_read(dest(env) / "s_S1_L001_R1_001.fastq.gz")
    [aside] = run_dir.parent.glob("key.failed-*")
    assert (aside / "logs").is_dir() and not (aside / "fastq").exists()


@pytest.mark.parametrize(
    "sample, runs",
    [("", "SRR1000001"), ("a__b", "SRR1000001"), ("s", ""), ("s", "GSM8180251"),
     ("s", "SRR1000001,SRR1000001"), ("s", ",".join(f"SRR100000{i}" for i in range(10)))],
)
def test_bad_inputs_are_refused_with_6_before_any_download(env, sample, runs):
    result = fetch(env, sample, runs)
    assert result.returncode == 6
    assert calls(env) == []


# --------------------------------------------------------------------------- #
# The image and the template
# --------------------------------------------------------------------------- #


def test_the_image_installs_it_with_fastq_dump_and_claim_run_dir():
    dockerfile = (CELLRANGER.parent / "Dockerfile").read_text()
    assert "COPY cellranger/fetch-sra.sh /usr/local/bin/fetch-sra" in dockerfile
    assert "COPY cellranger/claim-run-dir.sh /usr/local/bin/claim-run-dir" in dockerfile
    assert re.search(r"for tool in [^;]*\bfastq-dump\b", dockerfile)


def test_the_template_runs_it_into_the_runs_folder_without_s3_keys():
    template = (CELLRANGER / "cellranger-count-template.yaml").read_text()
    step = template.split("\n    - name: fetch-sra\n", 1)[1].split("\n    - name: ", 1)[0]
    assert "--, fetch-sra]" in step
    assert 'value: "/shared/runs/{{inputs.parameters.run-id}}"' in step
    assert "AWS_" not in step and "outputs:" not in step
    assert 'expression: "asInt(lastRetry.exitCode) == 10"' in step
    assert len(set(re.findall(r"cellranger:(\S+)", template))) == 1
