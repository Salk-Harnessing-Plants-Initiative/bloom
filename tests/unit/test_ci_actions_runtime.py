"""Every action a workflow uses runs on Node 24 and is pinned to a commit SHA.

Each `uses:` must look like `owner/repo@<40-char sha>  # vX.Y.Z`, and the version in
the comment must be at least the first release of that action built for Node 24.
Local actions (`./...`) and `docker://` images are skipped.

The offline checks trust the version comment. Run with CHECK_ACTION_PINS=1 to also
confirm, over the network, that each SHA is the commit its comment's tag points to.
"""

from __future__ import annotations

import os
import re
import subprocess
from collections.abc import Iterator
from pathlib import Path
from typing import NamedTuple

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
GITHUB_DIR = REPO_ROOT / ".github"

# First release of each action whose own runtime (or, for a composite action, every
# step it runs) is node24. Read from each action's action.yml at that tag.
FIRST_NODE24: dict[str, tuple[int, ...]] = {
    "actions/checkout": (5,),
    "actions/setup-node": (5,),
    "actions/upload-artifact": (6,),
    "actions/download-artifact": (7,),
    "actions/cache": (5,),
    "actions/github-script": (8,),
    "docker/build-push-action": (7,),
    "docker/setup-buildx-action": (4,),
    "docker/metadata-action": (6,),
    "docker/login-action": (4,),
    "peter-evans/create-pull-request": (8,),
    "marocchino/sticky-pull-request-comment": (3,),
    "astral-sh/setup-uv": (7,),
    "github/codeql-action": (4,),
    # Composite; v0.36.0 is the first whose inner actions/cache step is node24.
    "aquasecurity/trivy-action": (0, 36),
}

_USES_LINE = re.compile(
    r"^\s*(?:-\s+)?uses:\s*(?P<ref>[^\s#]+)\s*(?:#\s*(?P<comment>.*))?$"
)
_SHA = re.compile(r"^[0-9a-f]{40}$")
_VERSION = re.compile(r"^v(\d+(?:\.\d+)*)$")


class Use(NamedTuple):
    path: Path
    line: int
    ref: str
    comment: str

    def where(self) -> str:
        return f"{self.path.relative_to(REPO_ROOT)}:{self.line}"


def _files() -> list[Path]:
    workflows = sorted((GITHUB_DIR / "workflows").glob("*.y*ml"))
    actions = sorted((GITHUB_DIR / "actions").glob("*/action.y*ml"))
    return workflows + actions


def _uses_in(path: Path) -> Iterator[Use]:
    for n, line in enumerate(path.read_text().splitlines(), start=1):
        m = _USES_LINE.match(line)
        if m:
            yield Use(path, n, m["ref"].strip("'\""), (m["comment"] or "").strip())


def _parsed_uses(node: object) -> Iterator[str]:
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "uses" and isinstance(value, str):
                yield value
            else:
                yield from _parsed_uses(value)
    elif isinstance(node, list):
        for item in node:
            yield from _parsed_uses(item)


def _remote(uses: list[Use]) -> list[Use]:
    return [u for u in uses if not u.ref.startswith(("./", "docker://"))]


def _action(ref: str) -> str:
    # github/codeql-action/init@sha -> github/codeql-action
    return "/".join(ref.split("@", 1)[0].split("/")[:2])


ALL_USES = [u for p in _files() for u in _uses_in(p)]
REMOTE_USES = _remote(ALL_USES)


def test_workflows_use_actions():
    assert len(REMOTE_USES) > 50


@pytest.mark.parametrize("path", _files(), ids=lambda p: p.name)
def test_line_scan_finds_every_uses(path: Path):
    parsed = sorted(_parsed_uses(yaml.safe_load(path.read_text())))
    scanned = sorted(u.ref for u in _uses_in(path))
    assert (
        scanned == parsed
    ), f"{path.name}: a `uses:` line has a shape this test can't read"


def test_every_action_is_pinned_by_sha_with_a_version():
    bad = []
    for u in REMOTE_USES:
        sha = u.ref.split("@", 1)[1] if "@" in u.ref else ""
        if not _SHA.match(sha) or not _VERSION.match(u.comment):
            bad.append(f"{u.where()}  {u.ref}  # {u.comment}".rstrip("# "))
    assert not bad, "pin to `owner/repo@<40-char sha>  # vX.Y.Z`:\n" + "\n".join(bad)


def test_every_action_is_in_the_runtime_table():
    missing = sorted({_action(u.ref) for u in REMOTE_USES} - FIRST_NODE24.keys())
    assert (
        not missing
    ), f"add each action's first Node 24 release to FIRST_NODE24: {missing}"


def test_every_action_runs_on_node24():
    old = []
    for u in REMOTE_USES:
        floor = FIRST_NODE24.get(_action(u.ref))
        label = (
            u.comment or u.ref.split("@", 1)[-1]
        )  # a bare tag carries its own version
        m = _VERSION.match(label)
        if floor is None or m is None:
            continue  # reported by the tests above
        version = tuple(int(x) for x in m[1].split("."))
        if version[: len(floor)] < floor:
            need = "v" + ".".join(map(str, floor))
            old.append(f"{u.where()}  {_action(u.ref)} {label} (needs {need} or later)")
    assert not old, "actions below their first Node 24 release:\n" + "\n".join(old)


def _pins() -> dict[tuple[str, str, str], str]:
    """(repo, sha, tag) -> first place it is used, for every SHA pin with a version."""
    pins: dict[tuple[str, str, str], str] = {}
    for u in REMOTE_USES:
        sha = u.ref.split("@", 1)[1] if "@" in u.ref else ""
        if _SHA.match(sha) and _VERSION.match(u.comment):
            pins.setdefault((_action(u.ref), sha, u.comment), u.where())
    return pins


def _tag_commit(repo: str, tag: str) -> str | None:
    out = subprocess.run(
        ["git", "ls-remote", f"https://github.com/{repo}", f"refs/tags/{tag}*"],
        capture_output=True,
        text=True,
        timeout=60,
        check=True,
    ).stdout
    refs = {ref: sha for sha, ref in (line.split("\t") for line in out.splitlines())}
    # An annotated tag's commit is on the peeled `^{}` ref.
    return refs.get(f"refs/tags/{tag}^{{}}") or refs.get(f"refs/tags/{tag}")


@pytest.mark.skipif(
    not os.environ.get("CHECK_ACTION_PINS"),
    reason="network check; set CHECK_ACTION_PINS=1 to run",
)
def test_every_sha_is_the_release_its_comment_names():
    wrong = []
    for (repo, sha, tag), where in sorted(_pins().items()):
        commit = _tag_commit(repo, tag)
        if commit != sha:
            wrong.append(
                f"{where}  {repo}@{sha}  # {tag} (tag is {commit or 'missing'})"
            )
    assert not wrong, "SHA does not match its version comment:\n" + "\n".join(wrong)
