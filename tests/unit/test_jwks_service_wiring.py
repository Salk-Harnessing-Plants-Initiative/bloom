"""Each JWKS verifier must receive the key blob in the variable it actually reads.

The asymmetric-signing migration gave GoTrue an EC key, so every new access token
is ES256. Verifiers were then wired as if they all accept key material the same
way PostgREST does — `${JWT_JWKS:-${JWT_SECRET}}` in their `*_JWT_SECRET`. Only
PostgREST does. `storage` reads `JWT_JWKS` and `realtime` reads `API_JWT_JWKS`;
both treat their `*_JWT_SECRET` as an opaque symmetric secret.

The consequence is silent and total: with no JWKS parsed, storage-api's
accepted-`alg` list is built from the (absent) key types and stays HS256-only, so
it rejects every ES256 token with `"alg" (Algorithm) Header Parameter value not
allowed` before checking any signature. That broke the A4 pipeline's Storage
uploads and every logged-in user's `createSignedUrl()` call on staging (#646).

Both stacks are covered. The dev stack had the identical wiring and is only latent
because dev does not normally provision `JWT_KEYS` — it breaks the same way as soon
as someone generates a per-environment pair, which the tooling tells them to do.

The JWKS default is `null`, never empty. Realtime's boot seed does
`if v, do: Jason.decode!(v)` and `""` is truthy in Elixir, so an empty value raises
`Jason.DecodeError` inside `run.sh`'s `set -euo pipefail` seed step and the
container crash-loops before it ever serves traffic. `"null"` decodes to `nil` in
Elixir and to `null` in JS, so both runtimes read it as "no JWKS".
"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
COMPOSE_FILES = ("docker-compose.prod.yml", "docker-compose.dev.yml")
PR_CHECKS = REPO_ROOT / ".github" / "workflows" / "pr-checks.yml"

# Service -> the variable that service reads a JWKS from.
JWKS_VARS = {
    "storage": "JWT_JWKS",
    "realtime": "API_JWT_JWKS",
}

# Service -> the variable that must stay a plain symmetric secret.
SYMMETRIC_ONLY_VARS = {
    "storage": "PGRST_JWT_SECRET",
    "realtime": "API_JWT_SECRET",
    # No documented JWKS input; keeps the pre-migration secret.
    "supavisor": "API_JWT_SECRET",
}

# (compose file, service, variable) for every case below.
_JWKS_CASES = [
    (f, s, v) for f in COMPOSE_FILES for s, v in sorted(JWKS_VARS.items())
]
_SYMMETRIC_CASES = [
    (f, s, v) for f in COMPOSE_FILES for s, v in sorted(SYMMETRIC_ONLY_VARS.items())
]


def _ids(cases: list[tuple[str, str, str]]) -> list[str]:
    """`stack-service-var`, e.g. `prod-storage-JWT_JWKS`."""
    return [f"{c[0].split('.')[1]}-{c[1]}-{c[2]}" for c in cases]


def _env(compose: str, service: str) -> dict:
    path = REPO_ROOT / compose
    services = yaml.safe_load(path.read_text(encoding="utf-8"))["services"]
    assert service in services, f"{service} missing from {compose}"
    return services[service]["environment"]


@pytest.mark.parametrize("compose,service,var", _JWKS_CASES, ids=_ids(_JWKS_CASES))
def test_verifier_receives_jwks_in_the_variable_it_reads(
    compose: str, service: str, var: str
):
    env = _env(compose, service)
    assert var in env, (
        f"{compose}: {service} must receive the JWKS via {var} — it does not read "
        f"key material from its *_JWT_SECRET, so without {var} its accepted-alg "
        f"list stays HS256-only and every ES256 token is refused (#646)."
    )
    assert "JWT_JWKS" in str(env[var]), (
        f"{compose}: {service}'s {var} should interpolate ${{JWT_JWKS}}, "
        f"got {env[var]!r}."
    )


@pytest.mark.parametrize("compose,service,var", _JWKS_CASES, ids=_ids(_JWKS_CASES))
def test_jwks_default_is_null_not_empty(compose: str, service: str, var: str):
    """An empty default crash-loops realtime; see the module docstring."""
    value = str(_env(compose, service)[var])
    assert value == "${JWT_JWKS:-null}", (
        f"{compose}: {service}'s {var} must default to `null`, not empty — "
        f"realtime's boot seed does `if v, do: Jason.decode!(v)` and \"\" is truthy "
        f"in Elixir, so an empty value raises Jason.DecodeError under "
        f"`set -euo pipefail` and the container never starts. Got {value!r}."
    )


@pytest.mark.parametrize(
    "compose,service,var", _SYMMETRIC_CASES, ids=_ids(_SYMMETRIC_CASES)
)
def test_symmetric_secret_vars_stay_symmetric(compose: str, service: str, var: str):
    env = _env(compose, service)
    # Presence matters as much as the value: dropping the variable entirely would
    # break every kid-less HS256 token (ANON_KEY, SERVICE_ROLE_KEY) while a
    # substring-only assertion stayed green.
    assert var in env, (
        f"{compose}: {service} must keep {var} — it is the fallback key for "
        f"kid-less HS256 tokens such as ANON_KEY and SERVICE_ROLE_KEY (#646)."
    )
    assert "JWT_JWKS" not in str(env[var]), (
        f"{compose}: {service}'s {var} is read as an opaque symmetric secret, so a "
        f"JWKS there is unusable — it cannot verify ES256 and leaves HS256 tokens "
        f"checked against the wrong key. Pass the JWKS via "
        f"{JWKS_VARS.get(service, 'the service-specific JWKS variable')} instead "
        f"and keep {var} on ${{JWT_SECRET}} (#646)."
    )


@pytest.mark.parametrize("compose", COMPOSE_FILES)
def test_postgrest_keeps_jwks_fallback_in_its_secret(compose: str):
    """PostgREST is the one verifier that accepts a secret, a JWK, or a JWKS."""
    secret = str(_env(compose, "rest")["PGRST_JWT_SECRET"])
    assert "JWT_JWKS" in secret and "JWT_SECRET" in secret, (
        f"{compose}: rest's PGRST_JWT_SECRET should stay "
        f"${{JWT_JWKS:-${{JWT_SECRET}}}} so it verifies ES256 and pre-migration "
        f"HS256 tokens, got {secret!r}."
    )


@pytest.mark.parametrize("compose", COMPOSE_FILES)
def test_auth_is_the_only_service_holding_signing_keys(compose: str):
    """The EC private key signs; nothing else should ever receive JWT_KEYS."""
    services = yaml.safe_load(
        (REPO_ROOT / compose).read_text(encoding="utf-8")
    )["services"]
    holders = {
        name
        for name, spec in services.items()
        if any(
            "JWT_KEYS" in str(v) for v in (spec.get("environment") or {}).values()
        )
    }
    assert holders == {"auth"}, (
        f"{compose}: only auth may hold the signing key (JWT_KEYS); found "
        f"{sorted(holders)}. Verifiers take the public JWKS via JWT_JWKS."
    )


# --- CI must run the stack in the shape that can fail ------------------------
# Everything above is a string assertion over a compose file. None of it can
# fail the way #646 failed, because the bug lives in what the container does
# with the JWKS it parses. That needs a booted stack holding a real key pair —
# which CI did not have: GoTrue signed HS256, so no job ever produced an ES256
# token and compose-health-check passed identically either way.


def _health_check_steps() -> list[dict]:
    workflow = yaml.safe_load(PR_CHECKS.read_text(encoding="utf-8"))
    return workflow["jobs"]["compose-health-check"]["steps"]


def _provisioning_step_index() -> int | None:
    """Index of the step that writes both key variables into `.env.ci`."""
    for i, step in enumerate(_health_check_steps()):
        run = str(step.get("run", ""))
        if "JWT_KEYS=" in run and "JWT_JWKS=" in run and ".env.ci" in run:
            return i
    return None


def test_ci_provisions_an_asymmetric_key_pair():
    assert _provisioning_step_index() is not None, (
        "compose-health-check must write JWT_KEYS and JWT_JWKS into .env.ci. "
        "Without a pair, CI's GoTrue signs HS256 and never issues the ES256 "
        "token Storage refused, so nothing in CI can reproduce #646."
    )


def test_ci_provisions_the_key_pair_before_the_stack_starts():
    """Compose reads the env file when it creates a container.

    A pair written after the first `up -d` reaches nothing already running —
    the same recreate-vs-restart trap that makes this fix a redeploy.
    """
    provisioned = _provisioning_step_index()
    assert provisioned is not None  # covered by the test above
    first_up = next(
        (
            i
            for i, step in enumerate(_health_check_steps())
            if "up -d" in str(step.get("run", ""))
        ),
        None,
    )
    assert first_up is not None, "compose-health-check no longer starts a stack"
    assert provisioned < first_up, (
        f"the key pair is written at step {provisioned}, after the first "
        f"`up -d` at step {first_up} — containers created before it never see "
        f"it, and the ES256 coverage silently disappears."
    )
