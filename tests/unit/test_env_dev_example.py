"""`.env.dev.example` must be a complete, secret-free template for the dev stack.

A committed template is the entry point for a fresh clone (issue #104): `make
init` copies it to `.env.dev` and fills the CHANGEME values. The template must
therefore (a) list every variable `docker-compose.dev.yml` requires, (b) contain
no real secrets, and (c) stay LF so it is parsed identically on every platform.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
EXAMPLE = REPO_ROOT / ".env.dev.example"
COMPOSE = REPO_ROOT / "docker-compose.dev.yml"

# Same extraction regex used by tests/unit/test_env_defaults.py for the prod
# side — matches ${VAR}, ${VAR:-default}, ${VAR-default}.
_VAR_RE = re.compile(r"\$\{([A-Z][A-Z0-9_]*)(?::?-[^}]*)?\}")
# A reference with a default (`${VAR:-x}` / `${VAR-x}`) is optional — compose
# supplies a value — so it need not appear in the template.
_DEFAULTED_RE = re.compile(r"\$\{([A-Z][A-Z0-9_]*):?-[^}]*\}")

# Vars compose references but that are supplied by other means; excluded from
# the "must be in the template" set, each with a reason.
_EXCLUDE = {
    "COMPOSE_PROJECT_NAME",  # compose-internal project name
}


def _parse_keys(text: str) -> set[str]:
    keys = set()
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        keys.add(line.split("=", 1)[0].strip())
    return keys


def _required_compose_vars() -> set[str]:
    compose = COMPOSE.read_text(encoding="utf-8")
    all_refs = set(_VAR_RE.findall(compose))
    defaulted = set(_DEFAULTED_RE.findall(compose))
    return all_refs - defaulted - _EXCLUDE


def test_example_exists():
    assert EXAMPLE.exists(), ".env.dev.example must be committed (issue #104)"


def test_example_covers_every_required_compose_var():
    template_keys = _parse_keys(EXAMPLE.read_text(encoding="utf-8"))
    required = _required_compose_vars()
    missing = required - template_keys
    assert not missing, (
        f".env.dev.example is missing required compose vars: {sorted(missing)}"
    )


def test_example_has_no_real_secrets():
    """Every value must be a placeholder, plain config, or blank — never a real
    secret. Real secrets look like JWTs (eyJ...) or long random hex/base64."""
    offenders = []
    jwt_re = re.compile(r"\beyJ[A-Za-z0-9_-]{10,}")
    longhex_re = re.compile(r"^[A-Fa-f0-9]{32,}$")
    longb64_re = re.compile(r"^[A-Za-z0-9+/]{32,}={0,2}$")
    for line in EXAMPLE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.strip()
        if not value or value == "CHANGEME":
            continue
        if jwt_re.search(value) or longhex_re.match(value) or longb64_re.match(value):
            offenders.append(key.strip())
    assert not offenders, (
        f".env.dev.example contains values that look like real secrets: "
        f"{offenders}. Use CHANGEME placeholders instead."
    )


def test_example_uses_canonical_host_port_var():
    text = EXAMPLE.read_text(encoding="utf-8")
    assert "POSTGRES_HOST_PORT" in text
    assert "POSTGRES_EXTERNAL_PORT" not in text


def _check_ignore(path: str) -> bool:
    """True if git ignores ``path`` (git check-ignore exits 0 when ignored)."""
    return (
        subprocess.run(
            ["git", "check-ignore", "-q", path], cwd=REPO_ROOT
        ).returncode
        == 0
    )


def test_gitignore_allows_template_but_blocks_real_env_files():
    assert not _check_ignore(".env.dev.example"), (
        ".env.dev.example must be tracked (a !negation in .gitignore)"
    )
    assert _check_ignore(".env.dev"), ".env.dev must stay git-ignored"
    assert _check_ignore(".env.dev.backup"), ".env.dev.backup must stay git-ignored"
    assert _check_ignore(".env.dev.backup.20260101120000"), (
        "timestamped .env.dev.backup.* files must stay git-ignored"
    )


def test_example_is_lf_only():
    eol = subprocess.run(
        ["git", "check-attr", "eol", "--", ".env.dev.example"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    assert eol.endswith("lf"), (
        f".env.dev.example must be LF (got {eol!r}); env parsing must be "
        f"platform-stable."
    )
