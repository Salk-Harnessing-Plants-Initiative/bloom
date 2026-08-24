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
- Resolved by dropping ``on: schedule`` for staging (which doesn't need frequent automatic
  refreshes) in favor of ``workflow_dispatch``-only there. An ``environment`` input (mirroring
  ``deploy.yml``'s own convention) selects which host/secret pair to use, closing the round-8
  gap with no new secret provisioning (``PROD_SERVICE_ROLE_KEY`` already existed).
- Round 9 found two gaps in that redesign itself: the job's ``concurrency.group`` wasn't
  scoped by environment (a staging dispatch and a production dispatch could cancel each
  other, despite touching entirely independent databases), and the job declared no
  ``environment:`` key, so it never went through this repo's GitHub Environment approval
  gates (required reviewers/wait timers) that ``deploy.yml``'s own staging/production jobs
  do. Both fixed; the ``environment`` input's ``default: 'staging'`` was also removed --
  forcing an explicit choice every dispatch, rather than silently refreshing staging when
  someone meant to pick production.
- **CORRECTED (bloom#708 investigation):** an earlier version of this docstring claimed
  ``workflow_dispatch``, unlike ``schedule:``, "can be triggered against ANY branch/ref
  containing the file, no promotion needed." That was wrong -- GitHub gates
  ``workflow_dispatch`` on default-branch presence exactly like ``schedule:`` is (GitHub's own
  docs: "To trigger the workflow_dispatch event, your workflow must be in the default
  branch"), confirmed against this repo directly while this file existed only on ``staging``
  (``gh api`` returned 404; ``gh workflow list --all`` didn't list it). Neither trigger type
  works until this file is promoted to ``main`` via this repo's normal ``staging -> main``
  practice.
- **bloom#708 (this same round): production now has an automatic daily ``on: schedule`` cron**
  (``cron: '17 0 * * *'``, deliberately off the top of the hour) rather than staying
  on-demand-only indefinitely; staging keeps none. Since a ``schedule`` event carries no
  ``github.event.inputs`` context, ``ENVIRONMENT``/the job's ``environment:`` key/
  ``concurrency.group`` all branch on ``github.event_name`` instead. The job's
  ``environment:`` key resolves to a **different** literal than ``ENVIRONMENT``/
  ``concurrency.group`` do for a scheduled run: the latter two resolve to ``'production'``
  (the actual database/host), while the job's ``environment:`` key resolves to
  ``'production-scheduled-refresh'`` -- a second, purpose-created, ungated GitHub
  Environment -- because routing a scheduled run through ``production``'s own
  ``required_reviewers`` gate would leave every unattended nightly run stuck "Waiting" for a
  human approval that will never come. See design.md's D8 addendum
  (fix-cyl-scan-traits-latest-rollup) for the full reasoning.

This test does six things:

1. Asserts ``on: schedule`` exists with the expected cron, that ``workflow_dispatch`` still
   coexists alongside it, and that the cron string is structurally valid.
2. Asserts neither ``STAGING_API_URL`` nor ``PROD_API_URL`` is a GitHub secret reference, and
   that each hardcoded literal matches its own environment's ``.env.*.defaults`` value -- so
   they can never silently drift apart.
3. Asserts both service-role keys are still sourced from real secrets, and that the run
   script actually resolves the right URL/key pair for whichever environment is dispatched.
4. Asserts ``ENVIRONMENT``/``concurrency.group`` (target-host) and the job's ``environment:``
   key (approval-gate name) resolve via the correct, DISTINCT expressions for both trigger
   types -- including a truth-table test that extracts the live literals from the YAML rather
   than hardcoding an independently-asserted expected value, and a structural check that the
   scheduled-only Environment name can't leak into the ``workflow_dispatch`` fallback branch.
5. Actually EXECUTES the extracted https:// guard under bash (round-7 review finding:
   asserting the guard's error string appears in the script text proves the text is
   present, not that the shell logic behaves -- mirrors the technique in
   ``tests/unit/test_deploy_kong_reload_on_config_change.py``'s
   ``TestKongfileChangedFailSafeDefault``) for both environments' literals.
6. Asserts the job still has no checkout step and empty ``permissions:``.
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

# bloom#708: a `schedule` event carries no `github.event.inputs` context at all, so
# both the target-host (which database to call) and the job's `environment:` key
# (which GitHub Environment gates the run) must branch on `github.event_name`
# instead of reading `github.event.inputs.environment` directly. These are TWO
# DISTINCT expressions, not one shared value: a `schedule` run always targets
# production, but must NOT go through `production`'s `required_reviewers` approval
# gate (there's no human present to click "Approve" on an unattended cron) -- so it
# resolves the job's `environment:` key to a second, purpose-created, ungated
# Environment (`production-scheduled-refresh`) instead of `production` itself.
TARGET_HOST_EXPR = (
    "${{ github.event_name == 'schedule' && 'production' || "
    "github.event.inputs.environment }}"
)
JOB_ENVIRONMENT_EXPR = (
    "${{ github.event_name == 'schedule' && 'production-scheduled-refresh' || "
    "github.event.inputs.environment }}"
)

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


def test_schedule_trigger_exists_for_production_only() -> None:
    """bloom#708: production gets an automatic daily cron; staging stays dispatch-only."""
    on = _on_block(_load_workflow())
    assert "schedule" in on, "on: schedule trigger expected for bloom#708's production cron"
    schedule = on["schedule"]
    assert len(schedule) == 1, f"expected exactly one schedule entry, got {len(schedule)}"
    assert schedule[0].get("cron") == "17 0 * * *", (
        "cron must be '17 0 * * *' (once daily, deliberately off the top of the hour -- "
        f"GitHub's own docs flag midnight UTC as a period of elevated scheduler load); got "
        f"{schedule[0].get('cron')!r}"
    )


def test_workflow_dispatch_still_present_alongside_schedule() -> None:
    """Adding the schedule trigger must not remove manual dispatch for either host."""
    on = _on_block(_load_workflow())
    assert "schedule" in on
    assert "workflow_dispatch" in on, "workflow_dispatch must remain alongside schedule"


def test_cron_expression_is_structurally_valid() -> None:
    """A copy-paste typo (wrong field count, out-of-range value) would otherwise only
    surface after promotion to `main`, possibly not until a missed run."""
    cron = _on_block(_load_workflow())["schedule"][0]["cron"]
    fields = cron.split()
    assert len(fields) == 5, f"cron must have exactly 5 fields, got {fields!r} from {cron!r}"
    for field, (lo, hi) in zip(fields, [(0, 59), (0, 23), (1, 31), (1, 12), (0, 6)]):
        if field == "*":
            continue
        assert field.isdigit(), f"cron field {field!r} is neither '*' nor numeric"
        assert lo <= int(field) <= hi, f"cron field {field!r} out of range [{lo}, {hi}]"


def test_target_host_expression_matches_between_env_var_and_concurrency_group() -> None:
    """ENVIRONMENT (drives the bash case/esac) and concurrency.group's host suffix
    must resolve identically, or a scheduled run could hit a different host than the
    concurrency group it's serialized against expects."""
    env = _step(_load_workflow())["env"]
    assert env.get("ENVIRONMENT") == TARGET_HOST_EXPR, (
        f"ENVIRONMENT must be {TARGET_HOST_EXPR!r}; got {env.get('ENVIRONMENT')!r}"
    )

    concurrency = _load_workflow()["concurrency"]
    group = str(concurrency.get("group", ""))
    prefix = "refresh-cyl-experiment-trait-counts-"
    assert group.startswith(prefix), f"concurrency.group must start with {prefix!r}; got {group!r}"
    assert group[len(prefix) :] == TARGET_HOST_EXPR, (
        f"concurrency.group's host suffix must be the identical target-host expression "
        f"as ENVIRONMENT, not a different one; got {group[len(prefix):]!r}"
    )


_SCHEDULE_BRANCH_RE = re.compile(
    r"github\.event_name == 'schedule' && '([^']+)' \|\| github\.event\.inputs\.environment"
)


def _extract_schedule_branch_literal(expr: str) -> str:
    """Extract the literal a `schedule` event resolves to, from the LIVE expression string.

    Deliberately does not hardcode an independently-asserted expected value -- a test
    that just restates the intended logic in parallel Python, decoupled from the actual
    file content, would pass unchanged even if the real expression drifted (the same
    false-confidence failure mode this file's own history already caught once). If the
    expression doesn't match the expected `A == 'schedule' && '<literal>' || B` shape at
    all (e.g. the `&&`/`||` structure was changed), this fails loudly rather than
    silently returning something that happens to satisfy a caller's assertion.
    """
    match = _SCHEDULE_BRANCH_RE.search(expr)
    assert match, (
        "expression does not match the expected `github.event_name == 'schedule' && "
        f"'<literal>' || github.event.inputs.environment` shape -- got {expr!r}"
    )
    return match.group(1)


@pytest.mark.parametrize(
    "event_name, dispatch_input, expected_target_host, expected_job_environment",
    [
        ("schedule", None, "production", "production-scheduled-refresh"),
        ("workflow_dispatch", "staging", "staging", "staging"),
        # The case that specifically proves a manual dispatch to production is NOT
        # silently routed to the ungated production-scheduled-refresh Environment.
        ("workflow_dispatch", "production", "production", "production"),
    ],
)
def test_resolution_truth_table_for_schedule_vs_dispatch(
    event_name: str,
    dispatch_input: str | None,
    expected_target_host: str,
    expected_job_environment: str,
) -> None:
    """GitHub Actions expressions are evaluated server-side before the job's shell
    ever starts -- no local bash subprocess can exercise this `&&`/`||` ternary the
    way test_environment_input_resolves_to_the_right_url_and_key exercises the
    bash-level case/esac logic. This re-implements the ternary's resolution in
    Python, but the literals it resolves TO are extracted from the live YAML (see
    _extract_schedule_branch_literal), not hardcoded."""
    workflow = _load_workflow()
    target_host_literal = _extract_schedule_branch_literal(_step(workflow)["env"]["ENVIRONMENT"])
    job_environment_literal = _extract_schedule_branch_literal(
        workflow["jobs"][JOB]["environment"]
    )

    if event_name == "schedule":
        resolved_target_host = target_host_literal
        resolved_job_environment = job_environment_literal
    else:
        resolved_target_host = dispatch_input
        resolved_job_environment = dispatch_input

    assert resolved_target_host == expected_target_host
    assert resolved_job_environment == expected_job_environment


def test_scheduled_environment_name_appears_only_in_the_schedule_branch() -> None:
    """Guards against a copy-paste duplicating `production-scheduled-refresh` into the
    workflow_dispatch fallback branch, which would silently strip production's
    approval gate for a manual dispatch -- the more dangerous direction, opposite of
    the bug this section fixes."""
    job_env_expr = str(_load_workflow()["jobs"][JOB].get("environment", ""))
    assert job_env_expr.count("production-scheduled-refresh") == 1, (
        "'production-scheduled-refresh' must appear exactly once in the job's "
        f"environment: expression; got {job_env_expr!r}"
    )
    fallback = job_env_expr.rsplit("||", 1)[-1]
    assert "production-scheduled-refresh" not in fallback, (
        f"the workflow_dispatch fallback branch must not reference "
        f"'production-scheduled-refresh'; got fallback={fallback!r}"
    )
    assert "github.event.inputs.environment" in fallback, (
        f"the fallback branch must read the dispatch input verbatim; got {fallback!r}"
    )


def test_workflow_dispatch_has_environment_choice_input() -> None:
    """Mirrors deploy.yml's own environment-input convention, minus its default.

    No `default` (round 9): a dispatch with no explicit choice should force the
    dispatcher to pick, not silently land on staging when production was intended.
    """
    inputs = _on_block(_load_workflow())["workflow_dispatch"]["inputs"]
    env_input = inputs.get("environment")
    assert env_input is not None, "workflow_dispatch must declare an `environment` input"
    assert env_input.get("type") == "choice"
    assert env_input.get("required") is True
    assert set(env_input.get("options", [])) == {"staging", "production"}
    assert "default" not in env_input, (
        "environment input must have no default -- every dispatch should require an "
        f"explicit choice; got default={env_input.get('default')!r}"
    )


def test_job_has_environment_protection_gate() -> None:
    """The job's environment: key must resolve correctly for both trigger types.

    A workflow_dispatch run still requires the explicit `environment` input and goes
    through that Environment's own protection rules unchanged (round 9 fix). A
    `schedule` run resolves to `production-scheduled-refresh` instead of `production`
    itself (bloom#708) -- `production`'s own `required_reviewers` gate would otherwise
    leave every unattended nightly run stuck "Waiting" for a human approval that will
    never come.
    """
    job = _load_workflow()["jobs"][JOB]
    assert job.get("environment") == JOB_ENVIRONMENT_EXPR, (
        f"job.environment must be {JOB_ENVIRONMENT_EXPR!r} so a scheduled run resolves "
        f"to the ungated production-scheduled-refresh Environment while workflow_dispatch "
        f"keeps its existing approval gate; got {job.get('environment')!r}"
    )


def test_concurrency_group_is_scoped_by_environment() -> None:
    """concurrency.group stays keyed by target HOST, not by the Environment name.

    A staging dispatch must not be able to cancel an in-flight production one (round 9
    fix). A scheduled production run and a manual workflow_dispatch production run must
    still serialize against each other (both hit the same database) -- keying by the
    job-environment-name expression instead would let them race past each other with no
    serialization, since schedule and dispatch resolve to DIFFERENT Environment names
    for the same host (bloom#708).
    """
    concurrency = _load_workflow()["concurrency"]
    assert TARGET_HOST_EXPR in str(concurrency.get("group", "")), (
        "concurrency.group must contain the target-host expression, or dispatches "
        f"against the same host under different trigger types can race; got "
        f"{concurrency.get('group')!r}"
    )


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


_CURL_MARKER = "status=$(curl"


def _script_before_curl_call() -> str:
    """The run script up to (not including) the real `curl` call.

    Used to build test harnesses that exercise the environment/URL resolution logic
    without actually making a network request. Asserts the marker was found --
    without this, `str.split` on a missing separator returns the ORIGINAL string
    unchanged (not an error), which would silently hand back the full script
    including the live curl call, making a test harness fire a real HTTPS request
    against staging/production instead of failing loudly (round 9 finding).
    """
    script = _environment_dispatch_script()
    assert _CURL_MARKER in script, (
        f"could not find {_CURL_MARKER!r} in the run script -- if it was reformatted, "
        "update this marker rather than silently including the real curl call in a "
        "test harness"
    )
    return script.split(_CURL_MARKER, 1)[0]


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
    # Stop the script right after resolution, before the real curl call, and echo
    # what was resolved.
    cut = _script_before_curl_call()
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
    """An unexpected ENVIRONMENT value still errors loudly.

    The `type: choice` dropdown blocks this in the web UI, but the workflow_dispatch
    REST API (what `gh workflow run -f environment=...` calls) does not enforce
    `choice` constraints server-side -- a typo'd manual dispatch can genuinely reach
    this branch, not just a hypothetical future bug.
    """
    cut = _script_before_curl_call()
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
