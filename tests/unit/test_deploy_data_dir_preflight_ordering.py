"""Issue #474: docker-compose.prod.yml has the identical unguarded bloommcp
bind-mount shape (bloommcp/data/{TRAITS_DIR,PLOTS_DIR,ANALYSIS_OUTPUT}) that
issue #472/PR #473 fixed for the dev path only. This pins that the same
scripts/ensure_bloommcp_data_dirs.sh preflight runs, and runs BEFORE the
compose-up step, at all three additional call sites: deploy.yml's
deploy-production and deploy-staging jobs (SSH'd onto the real deploy host),
and pr-checks.yml's compose-health-check job (a fresh GitHub-hosted runner
every run, so it hits the exact same "Docker auto-creates the missing
bind-mount source as root" mechanism on every PR).

Also pins issue #477's deploy-host migration step (renaming a pre-existing
bloommcp/data/SLEAP_OUT_CSV to TRAITS_DIR in place) runs BEFORE the preflight
above in both deploy.yml jobs — see the ordering rationale in
test_deploy_jobs_provision_data_dirs_before_compose_up.
"""

from __future__ import annotations

from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
DEPLOY_YML = REPO_ROOT / ".github" / "workflows" / "deploy.yml"
PR_CHECKS_YML = REPO_ROOT / ".github" / "workflows" / "pr-checks.yml"

PREFLIGHT_MARKER = "ensure_bloommcp_data_dirs.sh"


def _load(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _step_run_text(step: dict) -> str:
    return str(step.get("run") or "")


def _find_step_index(steps: list[dict], predicate) -> int | None:
    for idx, step in enumerate(steps):
        if predicate(step):
            return idx
    return None


# (workflow path, job name, deploy-path secret name, up-step matcher)
DEPLOY_CASES = [
    (
        DEPLOY_YML,
        "deploy-production",
        "PROD_DEPLOY_PATH",
        lambda step: step.get("name") == "Deploy production stack",
    ),
    (
        DEPLOY_YML,
        "deploy-staging",
        "STAGING_DEPLOY_PATH",
        lambda step: step.get("name") == "Deploy staging stack",
    ),
]


def test_deploy_jobs_provision_data_dirs_before_compose_up():
    workflow = _load(DEPLOY_YML)
    for _, job_name, deploy_path_secret, is_deploy_step in DEPLOY_CASES:
        job = workflow["jobs"][job_name]
        steps = job.get("steps") or []
        preflight_idx = _find_step_index(
            steps, lambda s: PREFLIGHT_MARKER in _step_run_text(s)
        )
        deploy_idx = _find_step_index(steps, is_deploy_step)
        assert preflight_idx is not None, (
            f"{job_name} has no step invoking {PREFLIGHT_MARKER} — the bloommcp "
            "data-dir preflight is missing (issue #474)."
        )
        assert deploy_idx is not None, (
            f"{job_name} has no step matching the expected deploy-stack step "
            "name; has it been renamed?"
        )
        assert preflight_idx < deploy_idx, (
            f"{job_name}'s {PREFLIGHT_MARKER} step (index {preflight_idx}) must "
            f"run BEFORE its compose-up step (index {deploy_idx})."
        )
        preflight_run = _step_run_text(steps[preflight_idx])
        assert f"cd ${{{{ secrets.{deploy_path_secret} }}}}" in preflight_run, (
            f"{job_name}'s preflight step must `cd` into "
            f"${{{{ secrets.{deploy_path_secret} }}}} — each SSH step is its own "
            "session, so this does not carry over from a prior step."
        )
        # Issue #477: bloommcp/data/ is gitignored, so a rename of its
        # SLEAP_OUT_CSV leaf to TRAITS_DIR is invisible to `git reset --hard`
        # on the deploy host. The migration step (rename old->new in place if
        # present) MUST run before the preflight above — otherwise the
        # preflight's `mkdir -p` would auto-create an empty TRAITS_DIR first,
        # and the migration's own `[ ! -e bloommcp/data/TRAITS_DIR ]` guard
        # would then see it already exists and skip renaming the real,
        # populated legacy directory, silently orphaning it.
        migrate_idx = _find_step_index(
            steps, lambda s: "SLEAP_OUT_CSV" in _step_run_text(s) and "mv " in _step_run_text(s)
        )
        assert migrate_idx is not None, (
            f"{job_name} has no step migrating a legacy SLEAP_OUT_CSV directory "
            "to TRAITS_DIR — issue #477's rename needs this on a real deploy "
            "host, since bloommcp/data/ is gitignored and `git reset --hard` "
            "never touches it."
        )
        assert migrate_idx < preflight_idx, (
            f"{job_name}'s legacy-directory migration step (index {migrate_idx}) "
            f"must run BEFORE the data-dir preflight (index {preflight_idx}), or "
            "the preflight will auto-create an empty TRAITS_DIR first and the "
            "migration will then skip renaming the real populated directory."
        )


def test_compose_health_check_provisions_data_dirs_before_compose_up():
    workflow = _load(PR_CHECKS_YML)
    job = workflow["jobs"]["compose-health-check"]
    steps = job.get("steps") or []
    preflight_idx = _find_step_index(
        steps, lambda s: PREFLIGHT_MARKER in _step_run_text(s)
    )
    minio_dir_idx = _find_step_index(
        steps, lambda s: s.get("name") == "Create MinIO data directory"
    )
    start_remaining_idx = _find_step_index(
        steps, lambda s: s.get("name") == "Start remaining services"
    )
    assert preflight_idx is not None, (
        f"compose-health-check has no step invoking {PREFLIGHT_MARKER} — "
        "bloommcp/data/ is gitignored and untracked, so a fresh runner will "
        "let Docker auto-create it (and its three leaves) as root, unwritable "
        "by the non-root bloommcp container user (issue #474)."
    )
    assert start_remaining_idx is not None, (
        "compose-health-check has no 'Start remaining services' step; has it "
        "been renamed?"
    )
    assert preflight_idx < start_remaining_idx, (
        f"compose-health-check's {PREFLIGHT_MARKER} step (index {preflight_idx}) "
        f"must run BEFORE 'Start remaining services' (index {start_remaining_idx})."
    )
    if minio_dir_idx is not None:
        assert preflight_idx > minio_dir_idx, (
            "the bloommcp data-dir preflight should sit immediately after "
            "'Create MinIO data directory' so the two analogous preflights "
            "stay adjacent and easy to compare."
        )
