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


def _has_heading_for(changelog_text: str, version: str) -> bool:
    # Anchored to the start of a line (an actual ATX heading), not a bare
    # substring match — a version string appearing merely as prose mid-
    # paragraph must NOT count as a real changelog entry.
    pattern = rf"^## \[{re.escape(version)}\]"
    return re.search(pattern, changelog_text, re.MULTILINE) is not None


def test_changelog_has_an_entry_for_the_current_version():
    version = _current_version()
    changelog = CHANGELOG.read_text(encoding="utf-8")
    assert _has_heading_for(changelog, version), (
        f"bloomcli/CHANGELOG.md has no '## [{version}]' heading matching "
        f"bloomcli/pyproject.toml's version ({version}) — release-bloomcli.yml's "
        f"validate-release job would reject a Release tagged to this version."
    )


def test_a_version_mentioned_only_in_prose_is_not_mistaken_for_a_heading():
    """Adversarial: the version string appearing mid-paragraph (not as an
    actual ATX heading at the start of a line) must not satisfy the check.
    """
    fake_changelog = (
        "## [Unreleased]\n\n"
        "Some text mentions release ## [0.1.0a2] should not count as a "
        "heading since it isn't at the start of a line.\n"
    )
    assert not _has_heading_for(fake_changelog, "0.1.0a2")


def test_a_real_heading_is_detected_even_with_a_trailing_date():
    fake_changelog = "## [Unreleased]\n\n## [0.1.0a2] - 2026-07-23\n\n### Added\n"
    assert _has_heading_for(fake_changelog, "0.1.0a2")
