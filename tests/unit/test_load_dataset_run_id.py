"""The load-dataset step for a run started from Bloom (BLOOM_RUN_ID set): it loads under the name
`bloom-dataset pick` chooses, tags the dataset with the run, and reports the dataset's id for the
status poller. Also bloom_dataset's own choices. bloomctl and bloom-dataset are fakes on PATH."""

import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
ARGO = REPO / "argo" / "scrna"
SCRIPT = ARGO / "analysis" / "load-dataset.sh"
sys.path.insert(0, str(ARGO / "analysis"))
# bloomctl's own naming rule, as the image installs it.
sys.path.insert(0, str(REPO / "bloomcli" / "src"))

from bloom_scrna_analysis import bloom_dataset  # noqa: E402

FAKE_BLOOMCTL = r"""#!/usr/bin/env bash
printf ' [%s]' "$@" > "${FAKE_OUT}"
exit "${FAKE_EXIT:-0}"
"""

# pick answers FAKE_PICK; id answers FAKE_ID; FAKE_DATASET_EXIT fails either.
FAKE_BLOOM_DATASET = r"""#!/usr/bin/env bash
echo "$*" >> "${FAKE_CALLS}"
[ -z "${FAKE_DATASET_EXIT:-}" ] || exit "${FAKE_DATASET_EXIT}"
if [ "$1" = pick ]; then echo "${FAKE_PICK}"; else echo "${FAKE_ID}"; fi
"""


@pytest.fixture
def env(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name, body in (("bloomctl", FAKE_BLOOMCTL), ("bloom-dataset", FAKE_BLOOM_DATASET)):
        fake = bin_dir / name
        fake.write_text(body)
        fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    run = tmp_path / "shared" / "runs" / "col0__tair10__u"
    (run / "h5ad").mkdir(parents=True)
    (run / "h5ad" / "col0.h5ad").write_bytes(b"h5ad")
    credentials = tmp_path / "credentials.txt"
    credentials.write_text("BLOOM_API_URL=https://bloom.test/api\n")
    return {
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "SHARED_RUNS": str(tmp_path / "shared" / "runs"),
        "BLOOM_CREDENTIALS": str(credentials),
        "SAMPLE": "col0",
        "RUN_ID": "col0__tair10__u",
        "DATASET_NAME": "Root atlas",
        "SPECIES_NAME": "Arabidopsis",
        "BLOOM_RUN_ID": "42",
        "OUTPUT_DIR": str(tmp_path / "outputs"),
        "FAKE_OUT": str(tmp_path / "bloomctl.out"),
        "FAKE_CALLS": str(tmp_path / "calls.txt"),
        "FAKE_PICK": "Root atlas",
        "FAKE_ID": "57",
    }


def _run(env, **extra):
    return subprocess.run(
        ["bash", str(SCRIPT)], env={**env, **extra}, capture_output=True, text=True
    )


def _bloomctl_args(env) -> str:
    out = Path(env["FAKE_OUT"])
    return out.read_text() if out.exists() else ""


def _reported(env) -> str:
    return (Path(env["OUTPUT_DIR"]) / "dataset-id").read_text().strip()


def test_a_run_from_bloom_loads_tagged_with_its_run_and_reports_its_dataset(env):
    result = _run(env)
    assert result.returncode == 0, result.stderr
    assert "[--name] [Root atlas]" in _bloomctl_args(env)
    assert _bloomctl_args(env).endswith("[--run-id] [42]")
    assert _reported(env) == "57"
    calls = Path(env["FAKE_CALLS"]).read_text().splitlines()
    assert calls == [
        "pick --name Root atlas --species Arabidopsis --run-id 42",
        "id --name Root atlas --species Arabidopsis --run-id 42",
    ]


def test_a_taken_name_loads_under_the_name_picked(env):
    result = _run(env, FAKE_PICK="Root atlas_v2")
    assert result.returncode == 0, result.stderr
    assert "[--name] [Root atlas_v2]" in _bloomctl_args(env)
    assert "'Root atlas' is taken, so this run loads as 'Root atlas_v2'" in result.stdout


def test_a_failed_load_reports_no_dataset(env):
    result = _run(env, FAKE_EXIT="1")
    assert result.returncode == 1
    assert _reported(env) == ""
    assert "id --name" not in Path(env["FAKE_CALLS"]).read_text()


def test_an_unknown_species_fails_before_loading_and_isnt_retried(env):
    result = _run(env, FAKE_DATASET_EXIT="6")
    assert result.returncode == 6
    assert _bloomctl_args(env) == ""


def test_a_run_started_by_hand_loads_as_named_untagged_and_reports_nothing(env):
    result = _run(env, BLOOM_RUN_ID="")
    assert result.returncode == 0, result.stderr
    assert "--run-id" not in _bloomctl_args(env)
    assert not Path(env["FAKE_CALLS"]).exists()
    assert _reported(env) == ""


def test_a_run_naming_no_dataset_reports_nothing(env):
    assert _run(env, DATASET_NAME="").returncode == 0
    assert _reported(env) == ""


# --------------------------------------------------------------------------- #
# bloom_dataset's choices
# --------------------------------------------------------------------------- #


def _row(id_, name, run=None):
    return {"id": id_, "name": name, "metadata": {} if run is None else {"rnaseq_run_id": run}}


def test_a_free_name_is_used_as_typed():
    assert bloom_dataset.choose_name([], "Root atlas", 42) == "Root atlas"


def test_a_taken_name_moves_to_the_first_free_version():
    rows = [_row(1, "Root atlas"), _row(2, "root atlas_v2 ")]
    assert bloom_dataset.choose_name(rows, "Root atlas", 42) == "Root atlas_v3"


def test_a_retry_finds_its_own_dataset_even_past_a_free_name():
    rows = [_row(1, "Root atlas", run=41), _row(3, "Root atlas_v3", run=42)]
    assert bloom_dataset.choose_name(rows, "Root atlas", 42) == "Root atlas_v3"


def test_the_id_is_the_runs_dataset_under_that_name():
    rows = [_row(1, "Root atlas_v2", run=41), _row(7, "Root atlas_v2", run=42)]
    assert bloom_dataset.dataset_id(rows, "root atlas_v2", 42) == 7


def test_no_dataset_for_the_run_is_a_lookup_error():
    with pytest.raises(LookupError, match="for run 42"):
        bloom_dataset.dataset_id([_row(1, "Root atlas", run=41)], "Root atlas", 42)
