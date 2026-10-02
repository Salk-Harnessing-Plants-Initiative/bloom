"""h5py and numpy have to reach everything that ships and checks bloomctl.

`scrna hdf5 upload` reads HDF5. They are core dependencies, so a plain install, the published
image and the dependency audit all get them with no extra to remember.
"""

import tomllib
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
PR_CHECKS = REPO_ROOT / ".github" / "workflows" / "pr-checks.yml"
PYPROJECT = Path(__file__).resolve().parents[1] / "pyproject.toml"
NEEDS = ("h5py", "numpy")


def _project() -> dict:
    return tomllib.loads(PYPROJECT.read_text())["project"]


def _audit_step() -> str:
    """The bloomcli audit's `run:` scalar, read as YAML so a comment cannot stand in for it."""
    workflow = yaml.safe_load(PR_CHECKS.read_text())
    for job in workflow.get("jobs", {}).values():
        for step in job.get("steps", []) or []:
            run = step.get("run") or ""
            if "bloomcli" in run and "pip-audit" in run:
                return run
    return ""


def test_a_plain_install_brings_what_reading_a_file_needs():
    named = " ".join(_project()["dependencies"])
    for package in NEEDS:
        assert package in named, f"{package} is not a core dependency: {_project()['dependencies']}"


def test_there_is_no_scrna_extra_to_forget():
    assert "scrna" not in _project().get("optional-dependencies", {})


@pytest.mark.skipif(not PR_CHECKS.exists(), reason="the workflows live outside this package")
def test_the_audit_does_not_export_the_test_extra():
    """--all-extras drags pytest into an audit of what ships."""
    run = _audit_step()
    assert run, "no bloomcli pip-audit step found in pr-checks.yml"
    assert "uv export" in run, f"the bloomcli audit no longer exports the lock file: {run}"
    assert "--all-extras" not in run, f"the bloomcli audit exports every extra, including test: {run}"
