"""Shape guard for bloommcp's Dockerfile.

bloommcp's runtime dependency set (matplotlib, numpy, scipy, and everything
else with native code in the tree) publishes prebuilt manylinux wheels for
this image's cp311 + x86_64/aarch64 target, so `uv sync` never compiles from
source (see openspec/changes/remove-bloommcp-unused-apt-deps, #590). This
guards against a future PR silently reintroducing a build toolchain that
nothing in the dependency tree actually needs.
"""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent.parent
DOCKERFILE = REPO_ROOT / "bloommcp" / "Dockerfile"


def test_no_apt_get_install():
    text = DOCKERFILE.read_text(encoding="utf-8")
    assert "apt-get install" not in text
