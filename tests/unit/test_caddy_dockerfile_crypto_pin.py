"""Shape guard for the x/crypto override in caddy's Dockerfile.

CVE-2026-56854 is a CRITICAL authentication bypass in `golang.org/x/crypto/ssh`,
fixed in 0.55.0. Caddy 2.11.3 pins 0.50.0 and 2.11.4 — the newest release at the
time of writing — still pins 0.52.0, so there is no Caddy version to upgrade to.
Because the binary is built here with xcaddy, the fix is to force the patched
module in at build time (bloom#775).

The point of this test is that the override is invisible: nothing in the image
or the Caddyfile refers to it, and dropping the line would look like tidying up
a redundant flag. The CVE scan would catch it, but only on a branch that runs
the image build, and only if someone reads a red gate rather than assuming it is
red for the usual reasons.

It also holds the path to a subpackage. `--with golang.org/x/crypto@v0.55.0`
reads like the obvious spelling and does not compile: xcaddy blank-imports the
path it is given, and the module root holds no Go package. Only the image build
proves the image builds, but this at least keeps the one shape that already got
through from getting through twice.

When a Caddy release pins 0.55.0 or later, bump the builder and delete both the
override and this file — the assertion below is meant to fail at that point, so
the removal is a decision rather than a side effect.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent.parent
DOCKERFILE = REPO_ROOT / "caddy" / "Dockerfile"

# The version the fix landed in. Not a floor to be relaxed: below this the
# vulnerable code is compiled into the binary that terminates TLS for the
# whole stack.
FIXED_VERSION = (0, 55, 0)

_OVERRIDE = re.compile(
    r"--with\s+golang\.org/x/crypto(?P<subpackage>(?:/[a-z0-9]+)*)"
    r"@v(?P<major>\d+)\.(?P<minor>\d+)\.(?P<patch>\d+)"
)


def _joined(strip_comments: bool = False) -> str:
    """The Dockerfile with line continuations collapsed.

    Comments are stripped BEFORE joining, not after. Docker drops a `#` line
    that sits inside a continuation, so commenting out one `--with` leaves the
    rest of the command working — and joining first would glue that line onto
    `xcaddy build` and hide the `#`. Getting this backwards is what made the
    first version of the comment test pass against a commented-out override.
    """
    text = DOCKERFILE.read_text(encoding="utf-8")
    if strip_comments:
        text = "\n".join(
            line for line in text.splitlines() if not line.lstrip().startswith("#")
        )
    return text.replace("\\\n", " ")


def test_the_dockerfile_exists():
    assert DOCKERFILE.is_file(), f"{DOCKERFILE} is missing"


def test_xcaddy_forces_a_patched_x_crypto():
    match = _OVERRIDE.search(_joined())
    assert match, (
        "caddy/Dockerfile no longer forces golang.org/x/crypto. Caddy's own pin "
        "is below 0.55.0, so without this the built binary carries "
        "CVE-2026-56854 and the CVE scan fails. If a Caddy release now pins "
        "0.55.0 or later, bump the builder image and delete this test with it."
    )
    version = tuple(int(match.group(part)) for part in ("major", "minor", "patch"))
    assert version >= FIXED_VERSION, (
        f"x/crypto is forced to v{'.'.join(map(str, version))}, which is below "
        f"the {'.'.join(map(str, FIXED_VERSION))} that fixes CVE-2026-56854"
    )


def test_the_override_names_a_subpackage_not_the_module_root():
    """`golang.org/x/crypto` has no Go package at its root — only subdirectories
    like `bcrypt` and `ssh` hold code. xcaddy blank-imports whatever path it is
    handed, so the bare module path fails with "cannot find module providing
    package" and no image is built at all. The version applies to the whole
    module either way, so naming a subpackage costs nothing."""
    match = _OVERRIDE.search(_joined(strip_comments=True))
    assert match, "the x/crypto override is gone from the xcaddy build"
    assert match.group("subpackage"), (
        "the override names golang.org/x/crypto itself, which has no Go package "
        "at its root — the image will not build. Name a subpackage Caddy already "
        "imports, e.g. golang.org/x/crypto/bcrypt@v0.55.0"
    )


def test_the_override_is_on_the_xcaddy_build_not_a_comment():
    """A `--with` inside a comment would satisfy a naive search and compile
    nothing — the same way a phrase left in a comment satisfied an earlier
    contract test elsewhere in this repo."""
    executable = _joined(strip_comments=True)
    assert _OVERRIDE.search(executable), (
        "the x/crypto override survives only in a comment, so nothing forces it"
    )
    assert "xcaddy build" in executable
