"""Shape guard for the Box object backup's image-publishing workflow.

docker-build-box-object-backup.yml runs only on push to staging, so no pull
request exercises it before it lands. This test is its pre-merge gate:
publish only from staging and only when the job changed, authenticate with
GITHUB_TOKEN alone, push one manifest, and scan the digest it pushed.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).parent.parent.parent
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "docker-build-box-object-backup.yml"

JOB_PATH = "scheduled-jobs/box-object-backup"
IMAGE = "ghcr.io/salk-harnessing-plants-initiative/box-object-backup"


def _load() -> dict:
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


def _on(wf: dict) -> dict:
    # PyYAML reads the bare key `on` as True (YAML 1.1).
    return wf.get("on") or wf.get(True)


def _job() -> dict:
    jobs = _load()["jobs"]
    assert list(jobs) == ["build-and-push"]
    return jobs["build-and-push"]


def _index(job: dict, action: str) -> int:
    found = [i for i, s in enumerate(job["steps"]) if action in str(s.get("uses", ""))]
    assert len(found) == 1, f"expected one {action} step, found {len(found)}"
    return found[0]


def test_triggers_are_push_and_dispatch_only():
    assert set(_on(_load())) == {"push", "workflow_dispatch"}


def test_push_is_staging_only_and_filtered_to_the_job():
    push = _on(_load())["push"]
    assert push["branches"] == ["staging"]
    assert push["paths"] == [f"{JOB_PATH}/**"]


def test_permissions_are_read_by_default_and_packages_write_on_the_job():
    assert _load()["permissions"] == {"contents": "read"}
    assert _job()["permissions"] == {"contents": "read", "packages": "write"}


def test_no_secret_other_than_github_token():
    names = set(re.findall(r"secrets\.(\w+)", WORKFLOW.read_text(encoding="utf-8")))
    assert names == {"GITHUB_TOKEN"}


def test_job_has_a_timeout():
    assert isinstance(_job().get("timeout-minutes"), int)


def test_tags_are_the_short_sha_and_staging_on_push():
    step = _job()["steps"][_index(_job(), "docker/metadata-action")]
    assert step["with"]["images"] == IMAGE
    tags = step["with"]["tags"]
    assert "type=raw,value=sha-${{ steps.sha.outputs.short }}" in tags
    assert "type=raw,value=staging,enable=${{ github.event_name == 'push' }}" in tags


def test_build_pushes_one_manifest_from_the_job_folder():
    step = _job()["steps"][_index(_job(), "docker/build-push-action")]
    with_ = step["with"]
    assert with_["context"] == f"./{JOB_PATH}"
    assert with_["file"] == f"{JOB_PATH}/Dockerfile"
    assert with_["push"] is True
    # Without it the pushed digest is an index with an attestation, not the image.
    assert with_["provenance"] is False
    assert step.get("id") == "build"


def test_pushed_digest_is_scanned_after_the_push():
    job = _job()
    build = _index(job, "docker/build-push-action")
    scan = _index(job, "aquasecurity/trivy-action")
    assert scan > build
    with_ = job["steps"][scan]["with"]
    assert with_["image-ref"] == f"{IMAGE}@${{{{ steps.build.outputs.digest }}}}"
    assert with_["severity"] == "CRITICAL"
    assert str(with_["exit-code"]) == "1"
    assert with_["trivyignores"] == ".trivyignore"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
