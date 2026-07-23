"""Standing regression guard: pyproject.toml's version has a changelog entry.

Mirrors release-bloomcli.yml's validate-release job check (tag/changelog
gate), but exercised on every PR from now on instead of only at release
time.
"""

from __future__ import annotations
import re
from pathlib import Path

BLOOMCLI_ROOT = Path(__file__).parent.parent
PYPROJECT = BLOOMCLI_ROOT / "pyproject.toml"
CHANGELOG = BLOOMCLI_ROOT / "CHANGELOG.md"


def _current_version() -> str:
    text = PYPROJECT.read_text(encoding="utf-8")
    match = re.search(r'^version\s*=\s*"([^"]+)"', text, re.MULTILINE)
    assert match, "no version field found in bloomcli/pyproject.toml"
    return match.group(1)


def test_changelog_has_an_entry_for_the_current_version():
    version = _current_version()
    changelog = CHANGELOG.read_text(encoding="utf-8")
    heading = f"## [{version}]"
    assert heading in changelog, (
        f"bloomcli/CHANGELOG.md has no {heading!r} heading matching "
        f"bloomcli/pyproject.toml's version ({version}) — release-bloomcli.yml's "
        f"validate-release job would reject a Release tagged to this version."
    )
