"""Read installed package versions for manifest provenance.

Installed-only: a version is recorded for a distribution only when it is
actually pip-installed. An uninstalled distribution is omitted (left `None`)
rather than recorded as `"unknown"` — a vendored/uninstalled package reads as
noise and obscures the real provenance trace.
"""

from importlib.metadata import PackageNotFoundError, version

from .schema import CodeVersions

# Distribution name (PyPI/pip) -> CodeVersions field name.
_TRACKED = {
    "bloommcp": "bloommcp",
    "supabase": "supabase",
    "sleap-roots-analyze": "sleap_roots_analyze",
    "sleap-roots-contracts": "sleap_roots_contracts",
}


def _installed_version(distribution: str):
    """Return the installed version of `distribution`, or None if absent."""
    try:
        return version(distribution)
    except PackageNotFoundError:
        return None


def get_code_versions() -> CodeVersions:
    """Return installed versions of the tracked distributions (installed-only)."""
    fields = {}
    for dist, field in _TRACKED.items():
        installed = _installed_version(dist)
        if installed is not None:
            fields[field] = installed
    return CodeVersions(**fields)
