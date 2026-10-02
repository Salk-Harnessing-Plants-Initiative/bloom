"""Tests for argo/scrna/cellranger/stage-fastqs.sh, which copies a run's FASTQs onto the
shared disk, from the run's S3 folder or from raw_reads/<sample>/.

aws is replaced by a stand-in on PATH that keeps "S3" in a local folder: `s3api
list-objects-v2` lists it with each file's size and an ETag made from its contents, and `s3
cp`/`s3 sync` copy out of it and log every copy. The real fastq-sample-prefix checks the
result.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest
import yaml

CELLRANGER = Path(__file__).resolve().parents[2] / "argo/scrna/cellranger"
SCRIPT = CELLRANGER / "stage-fastqs.sh"
URL = "s3://lab-data/run42/"
R1 = "col0_S1_L001_R1_001.fastq.gz"
R2 = "col0_S1_L001_R2_001.fastq.gz"


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

AWS = r"""#!/usr/bin/env bash
# aws s3api list-objects-v2 | s3 cp | s3 sync against ${FAKE_S3}, standing in for s3://
local_path() { echo "${FAKE_S3}/${1#s3://}"; }
# Every call is logged with whether it was signed.
signed=yes; for a in "$@"; do [ "$a" = --no-sign-request ] && signed=no; done
echo "$1 $2 signed=${signed}" >> "${FAKE_S3}/.calls"
if [ "$1" = s3api ] && [ "$2" = head-object ]; then
  while [ $# -gt 0 ]; do
    case "$1" in --bucket) bucket="$2" ;; --key) key="$2" ;; --if-match) want="$2" ;; esac
    shift
  done
  [ -z "${FAKE_HEAD_FAIL:-}" ] || { echo "An error occurred (403) when calling the HeadObject operation: Forbidden" >&2; exit 255; }
  have="\"$(md5sum < "${FAKE_S3}/${bucket}/${key}" | cut -d' ' -f1)\""
  if [ "${have}" != "${want}" ]; then
    echo "An error occurred (412) when calling the HeadObject operation: Precondition Failed" >&2
    exit 254
  fi
  exit 0
fi
if [ "$1" = s3api ]; then
  [ -z "${FAKE_LIST_FAIL:-}" ] || { echo "An error occurred (AccessDenied)" >&2; exit 255; }
  while [ $# -gt 0 ]; do
    case "$1" in --bucket) bucket="$2" ;; --prefix) prefix="$2" ;; --delimiter) delim="$2" ;; --max-keys) max="$2" ;; esac
    shift
  done
  # Each listing can be made to change the folder first, to stand in for a slow upload.
  if [ -n "${FAKE_ON_LIST:-}" ]; then bash -c "${FAKE_ON_LIST}"; fi
  python3 - "${FAKE_S3}/${bucket}" "${prefix}" "${delim:-}" "${max:-1000}" <<'PY'
import hashlib, json, os, sys
# Like S3: with a delimiter, keys below a subfolder come back as one CommonPrefix; a page
# holds at most max-keys keys and prefixes, and IsTruncated says there are more.
root, prefix, delim, max_keys = sys.argv[1], sys.argv[2], sys.argv[3], int(sys.argv[4])
folder = os.path.join(root, prefix)
entries = []
if os.path.isdir(folder):
    for dirpath, _, names in os.walk(folder):
        for name in names:
            rel = os.path.relpath(os.path.join(dirpath, name), folder)
            entries.append(rel)
contents, prefixes = [], set()
for rel in sorted(entries):
    if delim and delim in rel:
        prefixes.add(prefix + rel.split(delim, 1)[0] + delim)
        continue
    data = open(os.path.join(folder, rel), "rb").read()
    contents.append({"Key": prefix + rel, "Size": len(data),
                     "ETag": '"%s"' % hashlib.md5(data).hexdigest()})
page = (contents + [{"Prefix": p} for p in sorted(prefixes)])[:max_keys]
out = {"IsTruncated": len(contents) + len(prefixes) > max_keys}
if [c for c in page if "Key" in c]:
    out["Contents"] = [c for c in page if "Key" in c]
if [c for c in page if "Prefix" in c]:
    out["CommonPrefixes"] = [c for c in page if "Prefix" in c]
print(json.dumps(out))
PY
  exit 0
fi
[ "$1" = s3 ] || exit 2
cmd="$2"; shift 2
args=(); for a in "$@"; do case "$a" in --only-show-errors | --no-sign-request) ;; *) args+=("$a") ;; esac; done
case "$cmd" in
  cp)
    case "${args[0]}" in *${FAKE_CP_FAIL_ON:-/nothing/}*) echo "download failed" >&2; exit 1 ;; esac
    mkdir -p "$(dirname "${args[1]}")"
    cp "$(local_path "${args[0]}")" "${args[1]}"
    echo "cp ${args[0]}" >> "${FAKE_S3}/.copies"
    # Stand-ins for a file replaced, or a copy cut short, while it was being copied.
    case "${args[0]}" in *${FAKE_REPLACE_DURING_COPY:-/nothing/}*) echo "new reads" > "$(local_path "${args[0]}")" ;; esac
    case "${args[0]}" in *${FAKE_TRUNCATE_COPY:-/nothing/}*) : > "${args[1]}" ;; esac
    ;;
  sync)
    src="$(local_path "${args[0]}")"; mkdir -p "${args[1]}"
    [ -d "$src" ] && cp "$src"/* "${args[1]}" 2>/dev/null || true
    echo "sync ${args[0]}" >> "${FAKE_S3}/.copies"
    ;;
esac
"""


@pytest.fixture
def env(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    aws = bin_dir / "aws"
    aws.write_text(AWS)
    aws.chmod(aws.stat().st_mode | stat.S_IEXEC)
    (bin_dir / "fastq-sample-prefix").symlink_to(CELLRANGER / "fastq-sample-prefix.sh")
    (bin_dir / "stage-fastqs-lib").symlink_to(CELLRANGER / "stage_fastqs_lib.py")
    s3 = tmp_path / "s3"
    s3.mkdir()
    return {
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "FAKE_S3": str(s3),
        "SAMPLE": "col0",
        "DEST_DIR": str(tmp_path / "shared/runs/col0__ref__u/fastq/col0"),
        "WAIT_SECONDS": "0",
        "POLL_SECONDS": "0",
    }


def _put(env, url, name, data=b"reads"):
    path = Path(env["FAKE_S3"]) / url.removeprefix("s3://") / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def _describe(env, url, *names):
    """The files as the start API records them: name, size and ETag."""
    out = []
    for name in names:
        data = (Path(env["FAKE_S3"]) / url.removeprefix("s3://") / name).read_bytes()
        out.append({"name": name, "size": len(data), "etag": '"%s"' % hashlib.md5(data).hexdigest()})
    return json.dumps(out)


def _folder(env, *names):
    for name in names:
        _put(env, URL, name, data=name.encode())
    return {**env, "FASTQ_URL": URL, "FASTQ_FILES": _describe(env, URL, *names)}


def _run(env, **extra):
    return subprocess.run([BASH, str(SCRIPT)], env={**env, **extra}, capture_output=True, text=True)


def _copies(env):
    log = Path(env["FAKE_S3"]) / ".copies"
    return log.read_text().splitlines() if log.exists() else []


def _staged(env):
    dest = Path(env["DEST_DIR"])
    return sorted(p.name for p in dest.iterdir()) if dest.exists() else []


def _run_dir(env):
    return Path(env["DEST_DIR"]).parent.parent


def _calls(env):
    log = Path(env["FAKE_S3"]) / ".calls"
    return log.read_text().splitlines() if log.exists() else []


def _leave_a_failed_run(env, fastqs=("col0_S1_L002_R1_001.fastq.gz",)):
    """What a failed run on the same key leaves: its FASTQs, Cell Ranger's output, its log."""
    run = _run_dir(env)
    for name in fastqs:
        (run / "fastq/col0").mkdir(parents=True, exist_ok=True)
        (run / "fastq/col0" / name).write_text("old reads")
    (run / "outs").mkdir(parents=True, exist_ok=True)
    (run / "outs/_SUCCESS").write_text("")
    (run / "logs").mkdir(exist_ok=True)
    (run / "logs/count.log").write_text("cell ranger failed")
    (run / ".inputs").write_text("folder s3://lab-data/older/ abc\n")


# --------------------------------------------------------------------------- #
# An S3 folder
# --------------------------------------------------------------------------- #


def test_the_runs_files_are_copied_and_nothing_else(env):
    e = _folder(env, R1, R2)
    _put(env, URL, "md5sums.txt")
    result = _run(e)
    assert result.returncode == 0, result.stderr
    assert _staged(env) == [R1, R2]
    assert _copies(env) == [f"cp {URL}{R1}", f"cp {URL}{R2}"]
    assert "FASTQ prefix: col0" in result.stdout


def test_a_subfolder_is_not_read(env):
    e = _folder(env, R1, R2)
    _put(env, URL + "old/", "col0_S1_L002_R1_001.fastq.gz")
    assert _run(e).returncode == 0
    assert _staged(env) == [R1, R2]


def test_no_fastqs_after_waiting_exits_4(env):
    e = {**env, "FASTQ_URL": URL,
         "FASTQ_FILES": json.dumps([{"name": R1, "size": 1, "etag": '"a"'},
                                    {"name": R2, "size": 1, "etag": '"b"'}])}
    result = _run(e)
    assert result.returncode == 4
    assert "no FASTQs" in result.stderr
    assert _copies(env) == []


def test_it_waits_for_an_empty_folder_to_fill(env, tmp_path):
    e = _folder(env, R1, R2)
    folder = Path(env["FAKE_S3"]) / "lab-data/run42"
    held = tmp_path / "held"
    held.mkdir()
    for name in (R1, R2):
        shutil.move(str(folder / name), str(held / name))
    # The second listing finds both files in place, as an upload finishing would.
    flag = tmp_path / "listed"
    on_list = f"[ -e {flag} ] && mv {held}/* {folder}/ 2>/dev/null; touch {flag}"
    result = _run(e, WAIT_SECONDS="30", FAKE_ON_LIST=on_list)
    assert result.returncode == 0, result.stderr
    assert "Waiting for" in result.stdout and "(empty)" in result.stdout
    assert _staged(env) == [R1, R2]


def test_a_folder_grown_past_one_page_exits_8(env):
    e = _folder(env, R1, R2)
    for n in range(1000):
        _put(env, URL, f"note{n:04d}.txt", data=b"x")
    result = _run(e)
    assert result.returncode == 8
    assert "over 1000 files" in result.stderr
    assert _copies(env) == []


def test_it_waits_for_files_still_arriving(env, tmp_path):
    e = _folder(env, R1, R2)
    folder = Path(env["FAKE_S3"]) / "lab-data/run42"
    held = tmp_path / "held"
    held.mkdir()
    shutil.move(str(folder / R2), str(held / R2))
    # The second listing finds R2 back in place, as an upload finishing would.
    flag = tmp_path / "listed"
    on_list = f'[ -e {flag} ] && mv {held / R2} {folder / R2} 2>/dev/null; touch {flag}'
    result = _run(e, WAIT_SECONDS="30", FAKE_ON_LIST=on_list)
    assert result.returncode == 0, result.stderr
    assert "Waiting for" in result.stdout
    assert _staged(env) == [R1, R2]


@pytest.mark.parametrize("change, reason", [
    (lambda env: _put(env, URL, R1, data=b"other reads, same name"), "is "),
    (lambda env: _put(env, URL, R1, data=R1.encode()[::-1]), "replaced"),
    (lambda env: _put(env, URL, "col0_S1_L002_R1_001.fastq.gz"), "was added"),
])
def test_a_changed_folder_exits_8_before_copying(env, change, reason):
    e = _folder(env, R1, R2)
    change(env)
    result = _run(e)
    assert result.returncode == 8
    assert reason in result.stderr
    assert _copies(env) == []


def test_a_file_removed_since_the_start_exits_8(env):
    e = _folder(env, R1, R2)
    (Path(env["FAKE_S3"]) / "lab-data/run42" / R2).unlink()
    result = _run(e)
    assert result.returncode == 8
    assert f"missing {R2}" in result.stderr


def test_files_for_another_sample_exit_9_before_copying(env):
    e = _folder(env, R1, R2)
    result = _run(e, SAMPLE="col1")
    assert result.returncode == 9
    assert "are named for col0, not the run's sample 'col1'" in result.stderr
    assert _copies(env) == []


def test_a_file_replaced_while_it_was_copied_exits_8_and_its_copy_is_removed(env):
    e = _folder(env, R1, R2)
    result = _run(e, FAKE_REPLACE_DURING_COPY=R2)
    assert result.returncode == 8
    assert f"{R2} in {URL} changed while it was being copied" in result.stderr
    assert R2 not in _staged(env)


def test_a_copy_of_the_wrong_size_exits_8(env):
    e = _folder(env, R1, R2)
    result = _run(e, FAKE_TRUNCATE_COPY=R1)
    assert result.returncode == 8
    assert "not the" in result.stderr and "recorded" in result.stderr
    assert R1 not in _staged(env)


def test_a_copy_that_cant_be_checked_exits_10(env):
    e = _folder(env, R1, R2)
    result = _run(e, FAKE_HEAD_FAIL="1")
    assert result.returncode == 10
    assert "couldn't check" in result.stderr


def test_each_copy_is_checked_unsigned_against_its_etag(env):
    assert _run(_folder(env, R1, R2)).returncode == 0
    assert _calls(env).count("s3api head-object signed=no") == 2


def test_misnamed_fastqs_exit_7(env):
    e = _folder(env, R1, "col0_S1_L001_R3_001.fastq.gz")
    assert _run(e).returncode == 7


@pytest.mark.parametrize("url", [
    "s3://lab-data/", "s3://lab-data/run42", "https://lab-data.s3.amazonaws.com/run42/",
    "s3://lab-data/../x/",
])
def test_a_bad_folder_exits_6(env, url):
    """With a valid file list, so it's the folder that is refused."""
    files = json.dumps([{"name": R1, "size": 1, "etag": '"a"'}, {"name": R2, "size": 1, "etag": '"b"'}])
    result = _run(env, FASTQ_URL=url, FASTQ_FILES=files)
    assert result.returncode == 6
    assert "is not a folder like s3://bucket/folder/" in result.stderr
    assert _copies(env) == []


@pytest.mark.parametrize("files", ["", "[]", "not json"])
def test_a_folder_without_its_files_exits_6(env, files):
    assert _run(env, FASTQ_URL=URL, FASTQ_FILES=files).returncode == 6
    assert _copies(env) == []


@pytest.mark.parametrize("entry, words", [
    ({"name": R1, "size": 1}, "exactly name, size, etag"),
    ({"name": R1, "size": 1, "etag": '"a"', "key": "x"}, "exactly name, size, etag"),
    ({"name": R1, "size": 1.5, "etag": '"a"'}, "whole number"),
    ({"name": R1, "size": True, "etag": '"a"'}, "whole number"),
    ({"name": R1, "size": 1, "etag": ""}, "ETag"),
    ({"name": "sub/" + R1, "size": 1, "etag": '"a"'}, "plain file name"),
    ("not an object", "exactly name, size, etag"),
])
def test_a_malformed_file_entry_exits_6_without_a_retry(env, entry, words):
    files = json.dumps([entry, {"name": R2, "size": 1, "etag": '"b"'}])
    result = _run(env, FASTQ_URL=URL, FASTQ_FILES=files)
    assert result.returncode == 6
    assert words in result.stderr
    assert _copies(env) == []


def test_a_folder_that_cant_be_listed_exits_10(env):
    e = _folder(env, R1, R2)
    result = _run(e, FAKE_LIST_FAIL="1")
    assert result.returncode == 10
    assert "couldn't list" in result.stderr


def test_a_failed_copy_exits_10(env):
    e = _folder(env, R1, R2)
    assert _run(e, FAKE_CP_FAIL_ON=R2).returncode == 10


def test_nothing_is_written_to_s3(env):
    e = _folder(env, R1, R2)
    before = sorted(p.relative_to(env["FAKE_S3"]) for p in Path(env["FAKE_S3"]).rglob("*") if p.name not in (".copies", ".calls"))
    assert _run(e).returncode == 0
    after = sorted(p.relative_to(env["FAKE_S3"]) for p in Path(env["FAKE_S3"]).rglob("*") if p.name not in (".copies", ".calls"))
    assert after == before


def test_a_folder_run_reads_unsigned(env):
    e = _folder(env, R1, R2)
    assert _run(e).returncode == 0
    assert _calls(env) and all(c.endswith("signed=no") for c in _calls(env))


# --------------------------------------------------------------------------- #
# A folder left by an earlier run on the same key
# --------------------------------------------------------------------------- #


def test_another_runs_files_and_results_are_set_aside(env):
    _leave_a_failed_run(env)
    e = _folder(env, R1, R2)
    result = _run(e)
    assert result.returncode == 0, result.stderr
    run = _run_dir(env)
    # Only this run's reads, and none of the earlier run's results, are in the folder.
    assert _staged(env) == [R1, R2]
    assert not (run / "outs").exists() and not (run / "logs").exists()
    # The earlier run's log is kept beside it, marked failed; its FASTQs are gone.
    [aside] = [p for p in run.parent.iterdir() if p.name.startswith(run.name + ".failed-")]
    assert (aside / "logs/count.log").read_text() == "cell ranger failed"
    assert (aside / "outs/_SUCCESS").exists()
    assert not (aside / "fastq").exists()
    assert "Set aside an earlier run's folder" in result.stdout


def test_a_retry_on_the_same_inputs_keeps_its_work(env):
    e = _folder(env, R1, R2)
    assert _run(e).returncode == 0
    (_run_dir(env) / "outs").mkdir()
    (_run_dir(env) / "outs/_SUCCESS").write_text("")
    assert _run(e).returncode == 0
    assert (_run_dir(env) / "outs/_SUCCESS").exists()
    assert not [p for p in _run_dir(env).parent.iterdir() if ".failed-" in p.name]


def test_other_files_from_the_same_folder_change_the_inputs(env):
    e = _folder(env, R1, R2)
    assert _run(e).returncode == 0
    (_run_dir(env) / "outs").mkdir()
    changed = {**e, "FASTQ_FILES": e["FASTQ_FILES"].replace('"size": ', '"size": 1')}
    _run(changed)
    assert not (_run_dir(env) / "outs").exists()


def test_a_folder_without_a_record_of_its_inputs_is_set_aside(env):
    _leave_a_failed_run(env)
    (_run_dir(env) / ".inputs").unlink()
    assert _run(_folder(env, R1, R2)).returncode == 0
    assert _staged(env) == [R1, R2]


def test_this_runs_sra_download_is_kept(env):
    _leave_a_failed_run(env)
    (_run_dir(env) / "sra").mkdir()
    (_run_dir(env) / "sra/SRR1.sra").write_text("downloading")
    assert _run(_folder(env, R1, R2)).returncode == 0
    assert (_run_dir(env) / "sra/SRR1.sra").exists()


def test_raw_reads_runs_set_aside_another_runs_folder_too(env):
    raw = "s3://bloomv2-workflows/raw_reads/col0/"
    _put(env, raw, R1)
    _put(env, raw, R2)
    _leave_a_failed_run(env)
    assert _run(env).returncode == 0
    assert _staged(env) == [R1, R2]
    assert not (_run_dir(env) / "outs").exists()


# --------------------------------------------------------------------------- #
# raw_reads/<sample>/
# --------------------------------------------------------------------------- #


def test_without_a_folder_it_reads_raw_reads(env):
    raw = "s3://bloomv2-workflows/raw_reads/col0/"
    _put(env, raw, R1)
    _put(env, raw, R2)
    result = _run(env)
    assert result.returncode == 0, result.stderr
    assert _copies(env) == [f"sync {raw}"]
    assert _staged(env) == [R1, R2]
    # Bloom's own bucket is read with Bloom's key.
    assert _calls(env) == ["s3 sync signed=yes"]


def test_an_empty_raw_reads_folder_exits_4(env):
    result = _run(env)
    assert result.returncode == 4
    assert "raw_reads/col0/" in result.stderr


def test_sample_and_dest_are_required(env):
    assert _run(env, SAMPLE="").returncode == 6


# --------------------------------------------------------------------------- #
# The image and the template
# --------------------------------------------------------------------------- #

TEMPLATE = CELLRANGER / "cellranger-count-template.yaml"


def _template(name):
    doc = yaml.safe_load(TEMPLATE.read_text())
    return next(t for t in doc["spec"]["templates"] if t["name"] == name)


def test_the_image_installs_it_and_the_stage_step_runs_it():
    dockerfile = (CELLRANGER.parent / "Dockerfile").read_text()
    assert "COPY cellranger/stage-fastqs.sh /usr/local/bin/stage-fastqs" in dockerfile
    assert "/usr/local/bin/stage-fastqs" in dockerfile.split("RUN chmod +x", 1)[1].split("\n")[0]
    assert "COPY cellranger/stage_fastqs_lib.py /usr/local/bin/stage-fastqs-lib" in dockerfile
    assert "/usr/local/bin/stage-fastqs-lib" in dockerfile.split("RUN chmod +x", 1)[1].split("\n")[0]
    assert "stage-fastqs" in _template("stage-sample")["container"]["args"][0]


def test_the_sample_pipeline_passes_the_folder_to_the_stage_step():
    pipeline = _template("sample-pipeline")
    optional = {p["name"]: p.get("value") for p in pipeline["inputs"]["parameters"]}
    assert optional["fastq-url"] == "" and optional["fastq-files"] == ""
    stage = next(t for t in pipeline["dag"]["tasks"] if t["name"] == "stage")
    passed = {p["name"]: p["value"] for p in stage["arguments"]["parameters"]}
    assert passed["fastq-url"] == "{{inputs.parameters.fastq-url}}"
    assert passed["fastq-files"] == "{{inputs.parameters.fastq-files}}"


def test_the_stage_step_hands_the_folder_to_the_script():
    env = {e["name"]: e.get("value") for e in _template("stage-sample")["container"]["env"]}
    assert env["FASTQ_URL"] == "{{inputs.parameters.fastq-url}}"
    assert env["FASTQ_FILES"] == "{{inputs.parameters.fastq-files}}"


def test_the_stage_step_doesnt_retry_failures_a_retry_cant_fix():
    rule = _template("stage-sample")["retryStrategy"]["expression"]
    for code in (4, 6, 7, 8, 9):
        assert f"asInt(lastRetry.exitCode) != {code}" in rule
    assert "!= 10" not in rule


# --------------------------------------------------------------------------- #
# stage-fastqs-lib on its own
# --------------------------------------------------------------------------- #


def _lib():
    import importlib.util

    spec = importlib.util.spec_from_file_location("stage_fastqs_lib", CELLRANGER / "stage_fastqs_lib.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


FILES = [{"name": R1, "size": 3, "etag": '"a"'}, {"name": R2, "size": 4, "etag": '"b"'}]


def _listing(*objs, truncated=False):
    return {"IsTruncated": truncated,
            "Contents": [{"Key": "run42/" + k, "Size": size, "ETag": etag} for k, size, etag in objs]}


def test_compare_ignores_keys_below_the_folder_and_other_files():
    lib = _lib()
    listing = _listing((R1, 3, '"a"'), (R2, 4, '"b"'), ("old/" + R1, 9, '"z"'), ("md5sums.txt", 1, '"m"'))
    assert lib.compare(listing, "run42/", FILES) == "match"


@pytest.mark.parametrize("listing, state", [
    ({}, "empty"),
    (_listing((R1, 3, '"a"')), f"partial: {R2}"),
    (_listing((R1, 3, '"a"'), (R2, 5, '"b"')), f"changed: {R2} is 5 bytes, not 4"),
    (_listing((R1, 3, '"x"'), (R2, 4, '"b"')), f"changed: {R1} was replaced (its ETag differs)"),
    (_listing((R1, 3, '"a"'), (R2, 4, '"b"'), ("col0_S1_L002_R1_001.fastq.gz", 1, '"c"')),
     "changed: col0_S1_L002_R1_001.fastq.gz was added"),
    (_listing((R1, 3, '"a"'), (R2, 4, '"b"'), truncated=True), "changed: the folder now holds over 1000 files"),
])
def test_compare_says_how_the_folder_differs(listing, state):
    assert _lib().compare(listing, "run42/", FILES) == state


def test_other_samples_names_each_other_prefix_once():
    lib = _lib()
    files = FILES + [{"name": "col1_S1_L001_R1_001.fastq.gz", "size": 1, "etag": '"c"'},
                     {"name": "col1_S1_L001_R2_001.fastq.gz", "size": 1, "etag": '"d"'}]
    assert lib.other_samples(files, "col0") == ["col1"]
    assert lib.other_samples(FILES, "col0") == []


def test_a_good_list_has_no_problem():
    assert _lib().bad_list(FILES) is None
