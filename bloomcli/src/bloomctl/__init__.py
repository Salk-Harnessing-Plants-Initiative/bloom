"""bloomctl — the Bloom command-line tool (Python successor to @salk-hpi/bloom-cli)."""

from importlib.metadata import PackageNotFoundError, version

try:
    # Single source of truth: the version comes from the installed package
    # metadata (built from pyproject.toml), so `uv version --bump` can't drift
    # from what `bloomctl --version` prints.
    __version__ = version("bloomctl")
except PackageNotFoundError:  # running from a source tree that isn't installed
    __version__ = "0.0.0+unknown"
