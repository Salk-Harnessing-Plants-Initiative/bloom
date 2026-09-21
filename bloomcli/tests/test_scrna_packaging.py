"""The scrna extra has to reach the two places that ship and check it.

`scrna upload` reads HDF5, so it needs the optional extra. Both the published image and the
dependency audit install from the lock file, and neither takes extras unless told to — so a
new dependency can be shipped broken, and audited by nothing, with every check green.

These read the files as the tools do — comments stripped, the workflow parsed as YAML — so a
command that has been commented out cannot satisfy a guard by still being present as text.
"""

import tomllib
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
DOCKERFILE = REPO_ROOT / "bloomcli" / "Dockerfile"
PR_CHECKS = REPO_ROOT / ".github" / "workflows" / "pr-checks.yml"
PYPROJECT = Path(__file__).resolve().parents[1] / "pyproject.toml"
EXTRA = "scrna"
# What the extra exists to install; renaming or emptying it must fail a guard, not pass one.
NEEDS = ("h5py", "numpy")


def _commands(text: str) -> list[str]:
    """The Dockerfile's instructions, without comments."""
    return [line for line in text.splitlines() if not line.lstrip().startswith("#")]


def _audit_step() -> str:
    """The bloomcli audit's `run:` scalar, read as YAML so a comment cannot stand in for it."""
    workflow = yaml.safe_load(PR_CHECKS.read_text())
    for job in workflow.get("jobs", {}).values():
        for step in job.get("steps", []) or []:
            run = step.get("run") or ""
            if "bloomcli" in run and "pip-audit" in run:
                return run
    return ""


def test_the_extra_installs_what_reading_a_file_needs():
    """A guard on the extra's name proves nothing if the extra no longer holds the packages."""
    extras = tomllib.loads(PYPROJECT.read_text())["project"]["optional-dependencies"]
    assert EXTRA in extras, f"pyproject has no {EXTRA} extra"
    named = " ".join(extras[EXTRA])
    for package in NEEDS:
        assert package in named, f"the {EXTRA} extra no longer installs {package}: {extras[EXTRA]}"


def test_the_image_installs_the_scrna_extra():
    """Without it, `bloomctl scrna upload` inside the published image cannot check a file."""
    project_sync = [
        line for line in _commands(DOCKERFILE.read_text())
        if "uv sync" in line and "--no-install-project" not in line
    ]
    assert project_sync, "no `uv sync` installing the project in bloomcli/Dockerfile"
    assert all(
        f"--extra {EXTRA}" in line or "--all-extras" in line for line in project_sync
    ), f"bloomcli/Dockerfile installs the project without the {EXTRA} extra: {project_sync}"


@pytest.mark.skipif(not PR_CHECKS.exists(), reason="the workflows live outside this package")
def test_the_dependency_audit_sees_the_extra():
    """`uv export` drops optional dependencies, so the audit would never see h5py or numpy."""
    run = _audit_step()
    assert run, "no bloomcli pip-audit step found in pr-checks.yml"
    assert "uv export" in run, f"the bloomcli audit no longer exports the lock file: {run}"
    assert f"--extra {EXTRA}" in run or "--all-extras" in run, (
        f"the bloomcli audit exports no extras, so it cannot see them: {run}"
    )


@pytest.mark.skipif(not PR_CHECKS.exists(), reason="the workflows live outside this package")
def test_the_audit_does_not_export_the_test_extra():
    """--all-extras drags pytest into an audit of what ships; naming the extra is exact."""
    run = _audit_step()
    assert "--all-extras" not in run, (
        f"the bloomcli audit exports every extra, including test: {run}"
    )
