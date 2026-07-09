"""code_versions records installed distributions only — never "unknown".

Maps the spec "Installed-Only Code Versions" scenarios: installed dists are
recorded with no "unknown"; an uninstalled dist is omitted rather than recorded
as the literal "unknown".
"""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError

import bloom_mcp.storage.code_versions as cv


def test_installed_distributions_recorded_no_unknown():
    """analyze + contracts are recorded; no field is the literal 'unknown'."""
    versions = cv.get_code_versions()
    dumped = versions.model_dump(exclude_none=True)

    assert "unknown" not in dumped.values()
    assert dumped.get("sleap_roots_analyze")
    assert dumped.get("sleap_roots_contracts")
    assert dumped.get("bloommcp")


def test_uninstalled_distribution_omitted_not_unknown(monkeypatch):
    """A distribution that is not installed is omitted, not set to 'unknown'."""
    real_version = cv.version

    def fake_version(name: str) -> str:
        if name == "supabase":
            raise PackageNotFoundError(name)
        return real_version(name)

    monkeypatch.setattr(cv, "version", fake_version)

    dumped = cv.get_code_versions().model_dump(exclude_none=True)
    assert "supabase" not in dumped
    assert "unknown" not in dumped.values()
