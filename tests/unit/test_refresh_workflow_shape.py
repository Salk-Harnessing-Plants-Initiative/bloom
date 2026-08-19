"""Regression guard for ``.github/workflows/refresh-cyl-experiment-trait-counts.yml``.

bloom#637/#656 (fix-cyl-scan-traits-latest-rollup, design.md D8): this workflow's history
is round-by-round:

- Originally read its staging base URL from ``secrets.STAGING_API_URL``, a secret that was
  never actually provisioned. Fixed by hardcoding it as a literal -- ``API_EXTERNAL_URL`` in
  ``.env.staging.defaults`` is the same value, already committed, and already documented as
  non-sensitive by the Committed Defaults contract (see ``tests/unit/test_env_defaults.py``).
- Round 7 found the ``on: schedule`` trigger can't fire until this workflow file is promoted
  to the repo's default branch (``main``) -- ``schedule:`` only ever runs from that branch's
  copy, and this repo merges feature work to ``staging`` first.
- Round 8 found a bigger gap: even once promoted, the workflow only ever targeted staging's
  host -- production's cache would never refresh by any existing mechanism.
- Resolved by dropping ``on: schedule`` entirely (staging doesn't need frequent automatic
  refreshes right now, and a schedule would sit inert there anyway per the round-7 finding)
  in favor of ``workflow_dispatch``-only, which -- unlike ``schedule:`` -- can be triggered
  against ANY branch/ref containing the file, no promotion needed. An ``environment`` input
  (mirroring ``deploy.yml``'s own convention) selects which host/secret pair to use, closing
  the round-8 gap with no new secret provisioning (``PROD_SERVICE_ROLE_KEY`` already existed).
  A scheduled trigger for production specifically is tracked as a future follow-up
  (bloom#708), not carried here speculatively.

This test does four things:

1. Asserts there is no ``on: schedule`` trigger at all, and that ``workflow_dispatch`` has a
   required ``environment`` choice input (``staging``/``production``, default ``staging``).
2. Asserts neither ``STAGING_API_URL`` nor ``PROD_API_URL`` is a GitHub secret reference, and
   that each hardcoded literal matches its own environment's ``.env.*.defaults`` value -- so
   they can never silently drift apart.
3. Asserts both service-role keys are still sourced from real secrets, and that the run
   script actually resolves the right URL/key pair for whichever environment is dispatched.
4. Actually EXECUTES the extracted https:// guard under bash (round-7 review finding:
   asserting the guard's error string appears in the script text proves the text is
   present, not that the shell logic behaves -- mirrors the technique in
   ``tests/unit/test_deploy_kong_reload_on_config_change.py``'s
   ``TestKongfileChangedFailSafeDefault``) for both environments' literals.
"""

from __future__ import annotations

import re
import shlex
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).parent.parent.parent
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "refresh-cyl-experiment-trait-counts.yml"
STAGING_DEFAULTS = REPO_ROOT / ".env.staging.defaults"
PROD_DEFAULTS = REPO_ROOT / ".env.prod.defaults"
JOB = "refresh"

# See test_deploy_kong_reload_on_config_change.py's identical helper: `bash` can
# resolve to the WSL launcher shim rather than a real POSIX shell on some Windows
# dev machines, depending on which process's PATH is being searched.
_GIT_BASH_CANDIDATES = [
    r"C:\Program Files\Git\bin\bash.exe",
    r"C:\Program Files\Git\usr\bin\bash.exe",
]


def _bash_executable() -> str:
    for candidate in _GIT_BASH_CANDIDATES:
        if Path(candidate).exists():
            return candidate
    return shutil.which("bash") or "bash"


BASH = _bash_executable()


def _parse_env(path: Path) -> dict[str, str]:
    """Parse a KEY=VALUE file into a dict. Ignore blank/comment lines."""
    result: dict[str, str] = {}
    for raw in path.read_text().splitlines():
        stripped = raw.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, _, value = stripped.partition("=")
        result[key.strip()] = value.strip()
    return result


def _load_workflow() -> dict:
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


def _on_block(workflow: dict) -> dict:
    """PyYAML parses the bare key ``on`` as the boolean ``True`` (YAML 1.1)."""
    return workflow.get("on") or workflow.get(True)


def _step(workflow: dict) -> dict:
    steps = workflow["jobs"][JOB]["steps"]
    assert len(steps) == 1, f"expected exactly one step in job {JOB!r}, got {len(steps)}"
    return steps[0]


def test_no_schedule_trigger_only_workflow_dispatch() -> None:
    """Staging doesn't need frequent automatic refreshes; schedule: can't fire pre-promotion anyway."""
    on = _on_block(_load_workflow())
    assert "schedule" not in on, (
        "no on: schedule trigger expected -- schedule: only ever fires from the default "
        "branch (round 7 finding), and staging doesn't need frequent automatic refreshes "
        "right now (see bloom#708 for production's eventual automatic-refresh follow-up)."
    )
    assert "workflow_dispatch" in on, "workflow_dispatch must remain the only trigger"


def test_workflow_dispatch_has_environment_choice_input() -> None:
    """Mirrors deploy.yml's own environment-input convention."""
    inputs = _on_block(_load_workflow())["workflow_dispatch"]["inputs"]
    env_input = inputs.get("environment")
    assert env_input is not None, "workflow_dispatch must declare an `environment` input"
    assert env_input.get("type") == "choice"
    assert env_input.get("required") is True
    assert set(env_input.get("options", [])) == {"staging", "production"}
    assert env_input.get("default") == "staging"


def test_api_urls_are_hardcoded_and_match_each_environments_env_defaults() -> None:
    """Both hardcoded literals must match their own .env.*.defaults's API_EXTERNAL_URL."""
    env = _step(_load_workflow())["env"]
    for env_var, defaults_path in (
        ("STAGING_API_URL", STAGING_DEFAULTS),
        ("PROD_API_URL", PROD_DEFAULTS),
    ):
        hardcoded = env.get(env_var)
        assert hardcoded is not None, f"step must set env.{env_var}"
        assert not str(hardcoded).startswith("${{"), (
            f"{env_var} must be a literal string, not a GitHub Actions expression; "
            f"got {hardcoded!r}"
        )
        expected = _parse_env(defaults_path)["API_EXTERNAL_URL"]
        assert hardcoded == expected, (
            f"workflow's hardcoded {env_var} ({hardcoded!r}) has drifted from "
            f"{defaults_path.name}'s API_EXTERNAL_URL ({expected!r})"
        )
        assert hardcoded.startswith("https://"), (
            f"{env_var} must be https:// -- a service-role key (a full-bypass-RLS "
            "credential) is sent on every dispatched run."
        )


def test_no_secret_reference_for_either_api_url() -> None:
    """Neither URL needs secret-store protection -- both are public, stable, committed values."""
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "secrets.STAGING_API_URL" not in text
    assert "secrets.PROD_API_URL" not in text


def test_both_service_role_keys_still_sourced_from_real_secrets() -> None:
    """The actual credentials stay secret-backed; only the non-sensitive URLs were hardcoded."""
    env = _step(_load_workflow())["env"]
    assert env.get("STAGING_SERVICE_ROLE_KEY") == "${{ secrets.STAGING_SERVICE_ROLE_KEY }}"
    assert env.get("PROD_SERVICE_ROLE_KEY") == "${{ secrets.PROD_SERVICE_ROLE_KEY }}"


def test_https_guard_still_present_in_script() -> None:
    """A defensive check survives even though both literals are now visibly https://."""
    script = _step(_load_workflow())["run"]
    assert "https://*" in script and "must be https://" in script


def _environment_dispatch_script() -> str:
    """The run script, as standalone shell -- for exercising the environment case/esac logic."""
    return _step(_load_workflow())["run"]


def _https_guard_snippet() -> str:
    """Extract the API_URL https:// `case ... esac` guard as standalone shell.

    Non-greedy `.*?esac` stops at the FIRST `esac` after the match start, so this
    assumes no nested `case`/`esac` inside this guard -- true today. If a future
    edit nests one inside a branch here, this would hand back a truncated,
    unbalanced snippet and fail with a bash syntax error rather than a clear message.
    """
    script = _environment_dispatch_script()
    match = re.search(r'case "\$\{API_URL\}" in.*?esac', script, re.DOTALL)
    assert match, "could not locate the API_URL https:// case/esac guard in the run script"
    return match.group(0)


@pytest.mark.parametrize(
    "environment, api_url",
    [
        ("staging", "https://staging.bloom.salk.edu:8443/api"),
        ("production", "https://bloom.salk.edu/api"),
    ],
)
def test_environment_input_resolves_to_the_right_url_and_key(
    environment: str, api_url: str
) -> None:
    """The case "${ENVIRONMENT}" in ... esac dispatch picks the right URL/key pair.

    Runs the FULL script (through the https:// guard, stopping before the real
    curl call by injecting a stub) with ENVIRONMENT set, and confirms the resolved
    API_URL/SERVICE_ROLE_KEY match the expected environment -- not just that the
    case statement's text mentions both branches.
    """
    script = _environment_dispatch_script()
    # Stop the script right after resolution, before the real curl call, by cutting
    # everything from the first `status=$(curl` onward and echoing what was resolved.
    cut = script.split("status=$(curl", 1)[0]
    harness = (
        f'ENVIRONMENT={shlex.quote(environment)}\n'
        f'STAGING_API_URL="https://staging.bloom.salk.edu:8443/api"\n'
        f'PROD_API_URL="https://bloom.salk.edu/api"\n'
        f'STAGING_SERVICE_ROLE_KEY="staging-key"\n'
        f'PROD_SERVICE_ROLE_KEY="prod-key"\n'
        f"{cut}\n"
        'echo "RESOLVED_URL=${API_URL}"\n'
        'echo "RESOLVED_KEY=${SERVICE_ROLE_KEY}"\n'
    )
    result = subprocess.run(
        [BASH, "-c", harness],
        capture_output=True,
        text=True,
        timeout=5,
        check=False,
    )
    assert result.returncode == 0, (
        f"resolution for environment={environment!r} failed: "
        f"stdout={result.stdout!r} stderr={result.stderr!r}"
    )
    assert f"RESOLVED_URL={api_url}" in result.stdout, result.stdout
    expected_key = "staging-key" if environment == "staging" else "prod-key"
    assert f"RESOLVED_KEY={expected_key}" in result.stdout, result.stdout


def test_unknown_environment_fails_loudly() -> None:
    """An unexpected ENVIRONMENT value (should never happen given the choice input) still errors."""
    script = _environment_dispatch_script()
    cut = script.split("status=$(curl", 1)[0]
    harness = (
        'ENVIRONMENT="not-a-real-environment"\n'
        f'STAGING_API_URL="https://staging.bloom.salk.edu:8443/api"\n'
        f'PROD_API_URL="https://bloom.salk.edu/api"\n'
        f'STAGING_SERVICE_ROLE_KEY="staging-key"\n'
        f'PROD_SERVICE_ROLE_KEY="prod-key"\n'
        f"{cut}\n"
        'echo "UNREACHABLE"\n'
    )
    result = subprocess.run(
        [BASH, "-c", harness],
        capture_output=True,
        text=True,
        timeout=5,
        check=False,
    )
    assert result.returncode != 0
    assert "UNREACHABLE" not in result.stdout
    assert "Unknown environment" in result.stdout


@pytest.mark.parametrize(
    "url, should_reject",
    [
        ("http://staging.bloom.salk.edu:8443/api", True),
        ("staging.bloom.salk.edu:8443/api", True),
        ("https://staging.bloom.salk.edu:8443/api", False),
        ("https://bloom.salk.edu/api", False),
    ],
)
def test_https_guard_actually_rejects_non_https(url: str, should_reject: bool) -> None:
    """Executes the extracted guard under bash -- proves behavior, not just text presence.

    A future edit could change either hardcoded literal to a plain http:// URL and
    every text-presence-only check would still pass. This runs the actual
    `case`/`esac` logic against both bad and real values for both environments.
    """
    snippet = _https_guard_snippet()
    # shlex.quote, not manual "{url}" interpolation: these parametrize values are
    # fixed literals with no exploit path today, but this exact f-string-into-shell
    # pattern is easy to copy-paste into a context where the embedded value isn't
    # a hardcoded literal -- quote it properly so that copy never becomes unsafe.
    script = f"API_URL={shlex.quote(url)}\n{snippet}\necho \"GUARD_PASSED\""
    result = subprocess.run(
        [BASH, "-c", script],
        capture_output=True,
        text=True,
        timeout=5,
        check=False,
    )
    if should_reject:
        assert result.returncode != 0, (
            f"guard should have rejected {url!r} (non-https) but exited 0: "
            f"stdout={result.stdout!r} stderr={result.stderr!r}"
        )
        assert "must be https://" in result.stdout, result.stdout
        assert "GUARD_PASSED" not in result.stdout, (
            "guard rejected the URL but execution still reached the trailing "
            f"echo -- the case arm's exit didn't actually stop the script: {result.stdout!r}"
        )
    else:
        assert result.returncode == 0, (
            f"guard should have accepted {url!r} but exited "
            f"{result.returncode}: stdout={result.stdout!r} stderr={result.stderr!r}"
        )
        assert "GUARD_PASSED" in result.stdout, result.stdout


def test_job_still_has_no_checkout_and_empty_permissions() -> None:
    """The environment selector must not require adding a checkout step."""
    workflow = _load_workflow()
    perms = workflow.get("permissions")
    assert perms == {}, (
        f"job permissions must stay empty ({{}}) -- this job only makes an "
        f"outbound curl call and should never need repo-content access; got "
        f"{perms!r}"
    )
    steps = workflow["jobs"][JOB]["steps"]
    assert not any("actions/checkout" in str(s.get("uses", "")) for s in steps), (
        "no step should use actions/checkout -- both hardcoded literals make "
        "checkout unnecessary; adding one would silently widen this job's "
        "blast radius for values that never needed repo access."
    )
