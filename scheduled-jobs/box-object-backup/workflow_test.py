"""Tests for the cross-file contract between this job and its workflow.

`runlock.SKIP_MARKER` is printed by the Python and grepped by the YAML. That
is a contract spanning two files in two languages with nothing but a comment
holding it together — exactly the kind that rots silently. An earlier version
of this job carried a comment naming a workflow file that did not exist at
all, and nothing noticed.

These assertions are deliberately about *shape*, not behaviour: they cannot
prove the workflow runs, only that it still agrees with the code it drives.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from runlock import SKIP_MARKER

WORKFLOW = (
    Path(__file__).resolve().parents[2] / ".github" / "workflows" / "box-object-backup.yml"
)


@pytest.fixture(scope="module")
def workflow() -> str:
    if not WORKFLOW.exists():
        pytest.fail(
            f"{WORKFLOW} is missing. runlock.SKIP_MARKER documents a contract "
            "with this file; if the scheduling approach changed, update that "
            "comment too."
        )
    return WORKFLOW.read_text(encoding="utf-8")


class TestSkipMarkerContract:
    """The one string that has to match across the two files."""

    def test_the_workflow_greps_a_prefix_of_the_marker(self, workflow: str):
        # The YAML greps a plain ASCII prefix: the full marker carries an
        # em dash, which is a poor thing to put in a shell string literal.
        prefix = "box-object-backup: SKIPPED"
        assert SKIP_MARKER.startswith(prefix)
        assert prefix in workflow

    def test_a_stood_down_run_is_not_reported_as_success(self, workflow: str):
        # The whole point: a skipped run exits 0 exactly as a good one does,
        # so the summary must distinguish them or a months-long gap in the
        # mirror reads as months of green ticks.
        skipped_at = workflow.find("box-object-backup: SKIPPED")
        succeeded_at = workflow.find("**succeeded**")
        assert skipped_at != -1 and succeeded_at != -1
        assert skipped_at < succeeded_at, (
            "the skip check must be tested before the success branch, "
            "or a stood-down run reports as succeeded"
        )


class TestScheduleShape:
    def test_runs_before_the_postgres_dump(self, workflow: str):
        # Saturday (day 6). The Postgres dump is Sunday (day 0), and objects
        # must land first so every row in that dump has bytes behind it.
        assert 'cron: "17 2 * * 6"' in workflow

    def test_does_not_share_the_deploy_concurrency_group(self, workflow: str):
        # A stuck deploy must not cancel the mirror, and vice versa.
        assert "group: box-object-backup-" in workflow
        assert "group: deploy-bloom" not in workflow

    def test_the_scheduled_run_is_not_behind_an_approval_gate(self, workflow: str):
        # An unattended 02:00 run routed through a reviewer gate waits for an
        # approval nobody is awake to give, and the backup silently never runs.
        assert "production-scheduled-backup" in workflow


class TestRunInvocation:
    def test_every_scheduled_run_verifies(self, workflow: str):
        # --verify defaults to 0, so leaving it off means no scheduled run
        # ever checks that what it copied is actually on Box.
        #
        # Assert the INVOCATION, not the bare flag name: `--verify` also
        # appears in this file's comments and in the dispatch input's
        # description, so `"--verify" in workflow` stays true even after the
        # flag is dropped from the command. Verified by deleting it — that
        # weaker assertion did not go red.
        assert "--verify '${VERIFY}'" in workflow

    def test_the_seed_is_not_run_by_the_workflow(self, workflow: str):
        # --full is the multi-day pass. The self-hosted runner is shared with
        # deploys and must not be held for days; the seed runs detached on the
        # host and this workflow stands down against its lock.
        assert "--full" not in workflow

    def test_the_deploy_path_comes_from_a_secret(self, workflow: str):
        # Never a hardcoded path: an earlier installer assumed /data/bloom,
        # which is not where this repo deploys.
        assert "secrets.PROD_DEPLOY_PATH" in workflow
        assert "/data/bloom" not in workflow


class TestSupersededSchedulingIsGone:
    """The systemd approach was reversed; its files must not come back."""

    @pytest.mark.parametrize(
        "leftover",
        [
            "bloom-box-object-backup.service",
            "bloom-box-object-backup.timer",
            "install.sh",
        ],
    )
    def test_the_systemd_files_are_not_present(self, leftover: str):
        assert not (Path(__file__).parent / leftover).exists(), (
            f"{leftover} belongs to the superseded systemd design — shipping it "
            "alongside the workflow tells an operator to install infrastructure "
            "the team decided against"
        )
