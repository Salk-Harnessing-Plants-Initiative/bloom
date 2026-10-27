"""Everything that checks a scRNA dataset name must apply the loader's rule.

The name becomes part of a storage path, so bloomctl's loader refuses anything but letters,
digits, spaces, '.', '_' and '-', and names of dots alone. Two places check it earlier, so a
Cell Ranger run isn't refused hours later at its load step: the job form (TypeScript), as the
name is typed, and the workflows service (Python), when the run is requested. None can import
the others, so the patterns are pinned together here. Each copy also trims the name first, as
the loader does; that is covered by each side's own tests.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
LOADER = REPO_ROOT / "bloomcli" / "src" / "bloomctl" / "scrna" / "_writer.py"
SERVICE = REPO_ROOT / "services" / "workflows" / "scrna_cellranger.py"
WEB = REPO_ROOT / "web" / "lib" / "scrna-jobs.ts"

NAMES = ("DATASET_NAME_RULE", "DOTS_ONLY")


def _python_patterns(path: Path, names: dict[str, str]) -> dict[str, str]:
    """Each named constant's `re.compile(r"...")` pattern, under the name the web uses."""
    text = path.read_text(encoding="utf-8")
    found = {}
    for ours, theirs in names.items():
        match = re.search(rf'^{theirs} = re\.compile\(r"([^"]+)"\)', text, re.MULTILINE)
        assert match, f"no {theirs} in {path.relative_to(REPO_ROOT)}"
        found[ours] = match.group(1)
    return found


def _web_patterns() -> dict[str, str]:
    """Each constant's `/^...$/` literal, without its anchors."""
    text = WEB.read_text(encoding="utf-8")
    found = {}
    for name in NAMES:
        match = re.search(rf"^const {name} = /\^(.+)\$/;$", text, re.MULTILINE)
        assert match, f"no anchored {name} in {WEB.relative_to(REPO_ROOT)}"
        found[name] = match.group(1)
    return found


def _loader() -> dict[str, str]:
    return _python_patterns(
        LOADER, {"DATASET_NAME_RULE": "SAFE_DATASET_NAME", "DOTS_ONLY": "DOTS_ONLY"}
    )


def test_the_service_applies_the_loaders_rule():
    assert _python_patterns(SERVICE, {n: n for n in NAMES}) == _loader()


def test_the_job_form_applies_the_loaders_rule():
    assert _web_patterns() == _loader()
