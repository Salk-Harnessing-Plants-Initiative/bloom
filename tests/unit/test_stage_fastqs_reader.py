"""Tests for stage-fastqs.sh reading a folder a scientist shared with Bloom's read-only reader:
anonymously first, and as the reader (READER_AWS_*) once the anonymous listing is refused,
never with the pipeline's own key. Uses the fake `aws` from test_stage_fastqs.py."""

import os
import stat
from pathlib import Path

import pytest

from tests.unit import test_stage_fastqs as stage

pytestmark = pytest.mark.skipif(stage.BASH is None, reason="needs bash 4+ (the pipeline image has it)")

READER = {"READER_AWS_ACCESS_KEY_ID": "AKIAREADER", "READER_AWS_SECRET_ACCESS_KEY": "reader-secret"}
PIPELINE = {"AWS_ACCESS_KEY_ID": "AKIAPIPELINE", "AWS_SECRET_ACCESS_KEY": "pipeline-secret"}


@pytest.fixture
def env(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    aws = bin_dir / "aws"
    aws.write_text(stage.AWS)
    aws.chmod(aws.stat().st_mode | stat.S_IEXEC)
    (bin_dir / "fastq-sample-prefix").symlink_to(stage.CELLRANGER / "fastq-sample-prefix.sh")
    (bin_dir / "stage-fastqs-lib").symlink_to(stage.CELLRANGER / "stage_fastqs_lib.py")
    s3 = tmp_path / "s3"
    s3.mkdir()
    return {
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "FAKE_S3": str(s3),
        "SAMPLE": "col0",
        "DEST_DIR": str(tmp_path / "shared/runs/col0__ref__u/fastq/col0"),
        "WAIT_SECONDS": "0",
        "POLL_SECONDS": "0",
        **PIPELINE,
    }


def _keys(env):
    log = Path(env["FAKE_S3"]) / ".keys"
    return log.read_text().splitlines() if log.exists() else []


def test_a_shared_private_folder_is_read_as_the_reader(env):
    e = stage._folder(env, stage.R1, stage.R2)
    result = stage._run(e, FAKE_PRIVATE="AKIAREADER", **READER)
    assert result.returncode == 0, result.stderr
    assert stage._staged(env) == [stage.R1, stage.R2]
    assert "isn't public; reading it as Bloom's reader" in result.stdout
    assert "(read reader)" in result.stdout
    # The first listing is anonymous; every call after it is signed as the reader, never with
    # the pipeline's key (which is in the pod's environment).
    calls = stage._calls(env)
    assert calls[0] == "s3api list-objects-v2 signed=no"
    assert len(calls) > 1 and all(c.endswith("signed=yes") for c in calls[1:])
    for call, keys in list(zip(calls, _keys(env)))[1:]:
        assert "key=AKIAREADER secret=reader-secret" in keys, call


def test_a_public_folder_never_uses_the_reader(env):
    e = stage._folder(env, stage.R1, stage.R2)
    result = stage._run(e, **READER)
    assert result.returncode == 0, result.stderr
    assert all(c.endswith("signed=no") for c in stage._calls(env))
    assert "(read anonymous)" in result.stdout


def test_without_the_readers_keys_a_private_folder_fails_to_list(env):
    e = stage._folder(env, stage.R1, stage.R2)
    result = stage._run(e, FAKE_PRIVATE="AKIAREADER")
    assert result.returncode == 10
    assert "couldn't list" in result.stderr
    assert stage._calls(env) == ["s3api list-objects-v2 signed=no"], "never retried with the pipeline key"


def test_a_folder_the_reader_cant_read_either_fails_to_list(env):
    e = stage._folder(env, stage.R1, stage.R2)
    result = stage._run(e, FAKE_PRIVATE="AKIASOMEONEELSE", **READER)
    assert result.returncode == 10
    assert "couldn't list" in result.stderr
    assert len(stage._calls(env)) == 2, "one anonymous listing, one as the reader, then stop"


def test_a_listing_failure_that_isnt_access_denied_is_not_retried_as_the_reader(env):
    e = stage._folder(env, stage.R1, stage.R2)
    result = stage._run(e, FAKE_LIST_UNREACHABLE="1", **READER)
    assert result.returncode == 10
    assert stage._calls(env) == ["s3api list-objects-v2 signed=no"]
