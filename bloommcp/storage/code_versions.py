"""Read installed package versions for manifest provenance."""
from importlib.metadata import PackageNotFoundError, version

from .schema import CodeVersions


def _version_or_unknown(package_name: str) -> str:
    try:
        return version(package_name)
    except PackageNotFoundError:
        return "unknown"


def get_code_versions() -> CodeVersions:
    """Return installed versions of packages whose provenance is captured per run."""
    return CodeVersions(
        bloommcp=_version_or_unknown("bloommcp"),
        supabase=_version_or_unknown("supabase"),
    )
