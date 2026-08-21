"""Shape guard for bloommcp's Dockerfile.

bloommcp's runtime dependency set (matplotlib, numpy, scipy, and everything
else with native code in the tree) publishes prebuilt manylinux wheels for
this image's cp311 + x86_64/aarch64 target, so `uv sync` never compiles from
source (see openspec/changes/remove-bloommcp-unused-apt-deps, #590). This
guards against a future PR silently reintroducing a build toolchain that
nothing in the dependency tree actually needs.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent.parent
DOCKERFILE = REPO_ROOT / "bloommcp" / "Dockerfile"

# Matches `apt-get install`/`apt install`, with or without flags in between
# (e.g. `apt-get -y install`, `apt-get --no-install-recommends install`), and
# survives a Dockerfile line-continuation split (`apt-get \` + newline +
# `install`) once continuations are joined below.
_APT_INSTALL = re.compile(r"\bapt(?:-get)?\s+(?:-\S+\s+)*install\b")


def test_no_apt_get_install():
    text = DOCKERFILE.read_text(encoding="utf-8")
    joined = re.sub(r"\\\r?\n", " ", text)
    assert not _APT_INSTALL.search(joined)
