"""The scrna extra has to reach the two places that ship and check it.

`scrna upload` reads HDF5, so it needs the optional extra. Both the published image and the
dependency audit install from the lock file, and neither takes extras unless told to — so a
new dependency can be shipped broken, and audited by nothing, with every check green.
"""

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DOCKERFILE = REPO_ROOT / "bloomcli" / "Dockerfile"
PR_CHECKS = REPO_ROOT / ".github" / "workflows" / "pr-checks.yml"
EXTRA = "scrna"


def _sync_lines(text: str) -> list[str]:
    return [line for line in text.splitlines() if "uv sync" in line]


def test_the_image_installs_the_scrna_extra():
    """Without it, `bloomctl scrna upload` inside the published image cannot check a file."""
    project_sync = [
        line for line in _sync_lines(DOCKERFILE.read_text()) if "--no-install-project" not in line
    ]
    assert project_sync, "no `uv sync` installing the project in bloomcli/Dockerfile"
    assert all(
        f"--extra {EXTRA}" in line or "--all-extras" in line for line in project_sync
    ), f"bloomcli/Dockerfile installs the project without the {EXTRA} extra: {project_sync}"


def test_the_dependency_audit_sees_the_extra():
    """`uv export` drops optional dependencies, so the audit would never see h5py or numpy."""
    audit = [
        line for line in PR_CHECKS.read_text().splitlines()
        if "uv export" in line and "pip-audit" in line and "bloomcli" in line
    ]
    assert audit, "no bloomcli pip-audit step found in pr-checks.yml"
    assert all(
        "--all-extras" in line or f"--extra {EXTRA}" in line for line in audit
    ), f"the bloomcli audit exports no extras, so it cannot see them: {audit}"
