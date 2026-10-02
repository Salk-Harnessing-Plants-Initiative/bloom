"""bloom-web restarts on its own, has a bounded memory, and knows which build it is (bloom#1007).

The trait export (#865) runs inside the bloom-web process and holds its jobs and
finished zips in memory. Without a restart policy, one out-of-memory crash leaves
the whole website down until someone restarts the container by hand. Without a
limit, nothing outside the process bounds it. And each export's sidecar records
`generated_by.version` as package version `+BLOOM_WEB_BUILD_SHA`, read at runtime
(web/app/api/cyl/trait-export/jobs/route.ts), so the deploy has to set that
variable or every file just says "1.0.0".

Staging and production both run from docker-compose.prod.yml on one host, so
every assertion here covers both.
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path

import pytest
import yaml

from tests.unit._compose_helpers import _bytes

# `bash` can resolve to the WSL launcher shim on some Windows dev machines; see
# the identical helper in test_deploy_kong_reload_on_config_change.py.
_BASH = next(
    (
        c
        for c in (
            r"C:\Program Files\Git\bin\bash.exe",
            r"C:\Program Files\Git\usr\bin\bash.exe",
        )
        if Path(c).exists()
    ),
    shutil.which("bash") or "bash",
)

REPO_ROOT = Path(__file__).resolve().parents[2]
COMPOSE_FILE = REPO_ROOT / "docker-compose.prod.yml"
DEPLOY_YML = REPO_ROOT / ".github" / "workflows" / "deploy.yml"
DEFAULTS_FILES = (".env.prod.defaults", ".env.staging.defaults")
SERVICE = "bloom-web"
SHA_VAR = "BLOOM_WEB_BUILD_SHA"

# Node 20 sizes its JS heap from the container limit, at about half of it
# (node:20-alpine, measured 2026-10-01: 1536m -> 792 MiB heap, 3g -> 1584 MiB).
# Two running exports plus the Next.js baseline need ~1 GB of heap, so below 3g
# V8 would abort on "Reached heap limit" well before the container limit.
MEMORY_FLOOR_BYTES = 3 * (1 << 30)

# The deploy's compose commands run on the host inside `ssh ... "..."`, so the
# SHA must be computed there: `\$(` defers the substitution past the runner's
# shell. Assignment and export are separate because `export X=$(cmd)` returns
# export's status, so a failing git would pass `set -e` and stamp a bare version.
ASSIGN_RE = re.compile(rf"^{SHA_VAR}=\\\$\(git rev-parse --short HEAD\)$")
EXPORT_LINE = f"export {SHA_VAR}"
COMPOSE_UP_RE = re.compile(r"\bup -d\b.*--build")
CHECKOUT_RE = re.compile(r"\bgit (reset|checkout|pull)\b")
DEPLOY_JOBS = ("deploy-production", "deploy-staging")


def _service() -> dict:
    return yaml.safe_load(COMPOSE_FILE.read_text(encoding="utf-8"))["services"][SERVICE]


def _service_text() -> str:
    """bloom-web's block as written, comments included."""
    text = COMPOSE_FILE.read_text(encoding="utf-8")
    start = text.index(f"\n  {SERVICE}:\n")
    following = re.search(r"\n  [a-z][a-z0-9-]*:\n", text[start + 1 :])
    end = start + 1 + following.start() if following else len(text)
    return text[start:end]


def test_bloom_web_restarts_on_its_own():
    """Every other long-running service in the stack is `unless-stopped`; bloom-web
    was the one that stayed down after a crash."""
    assert _service().get("restart") == "unless-stopped", (
        f"{SERVICE} has restart={_service().get('restart')!r}; a crashed or "
        "OOM-killed web server would stay down until restarted by hand"
    )


def test_bloom_web_has_a_memory_limit_that_swap_cannot_lift():
    service = _service()

    assert "mem_limit" in service, f"{SERVICE} is uncapped"
    assert _bytes(service["mem_limit"]) >= MEMORY_FLOOR_BYTES, (
        f"{SERVICE} is capped at {service['mem_limit']!r}; Node sizes its heap at "
        "about half the limit, which is too small for two running exports"
    )
    assert (
        "memswap_limit" in service
    ), f"{SERVICE} has no memswap_limit, so Docker allows twice mem_limit in swap"
    assert _bytes(service["memswap_limit"]) == _bytes(service["mem_limit"]), (
        f"{SERVICE}'s memswap_limit leaves swap headroom, so a runaway process "
        "grinds against disk instead of being killed and restarted"
    )


def test_bloom_web_is_documented_as_one_replica():
    """Export jobs live in the process's memory: a second replica would answer
    "export not found" for a job started on the first."""
    service = _service()

    assert re.search(
        r"\b(one|single)[- ]replica\b", _service_text(), re.I
    ), f"{SERVICE} has no comment saying it must run as one replica"
    assert (
        service.get("deploy", {}).get("replicas", 1) == 1
    ), f"{SERVICE} declares more than one replica"
    assert service.get("scale", 1) == 1, f"{SERVICE} declares scale > 1"


def test_bloom_web_reads_the_build_sha_at_runtime():
    """The route reads it from process.env per request: an environment entry, not
    a build arg (Next.js inlines only NEXT_PUBLIC_* at build time)."""
    service = _service()

    assert (
        service["environment"].get(SHA_VAR) == f"${{{SHA_VAR}:-}}"
    ), f"{SERVICE}'s environment does not pass {SHA_VAR} through"
    assert SHA_VAR not in (
        service["build"].get("args") or {}
    ), f"{SHA_VAR} is a build arg; the route reads it at runtime"


@pytest.mark.parametrize("defaults_file", DEFAULTS_FILES)
def test_the_build_sha_has_a_visible_fallback(defaults_file):
    """scripts/validate_env.sh rejects an empty value for any ${VAR} in compose.
    `unknown` is the value a hand-run `docker compose up` gets, so its sidecars say
    `1.0.0+unknown` rather than passing as a dev build."""
    lines = (REPO_ROOT / defaults_file).read_text(encoding="utf-8").splitlines()

    assert f"{SHA_VAR}=unknown" in lines, (
        f"{defaults_file} has no `{SHA_VAR}=unknown`; the deploy's env validator "
        "would reject the empty value"
    )


def _compose_up_steps() -> list[tuple[str, str, str]]:
    """(job, step name, run block) for every step that runs `up -d --build`."""
    jobs = yaml.safe_load(DEPLOY_YML.read_text(encoding="utf-8"))["jobs"]
    found = []
    for job in DEPLOY_JOBS:
        for step in jobs[job]["steps"]:
            run = step.get("run", "")
            if COMPOSE_UP_RE.search(run):
                found.append((job, step["name"], run))
    return found


def test_every_compose_up_is_found():
    """Forward deploy and rollback, in each job. If this count changes, the
    export below must follow the new call too."""
    found = [(job, name) for job, name, _ in _compose_up_steps()]

    assert found == [
        ("deploy-production", "Deploy production stack"),
        ("deploy-production", "Rollback on failure"),
        ("deploy-staging", "Deploy staging stack"),
        ("deploy-staging", "Rollback on failure"),
    ]


@pytest.mark.parametrize(
    "job,step_name,run",
    _compose_up_steps(),
    ids=[f"{job}:{name}" for job, name, _ in _compose_up_steps()],
)
def test_every_compose_up_stamps_the_checked_out_sha(job, step_name, run):
    """The SHA is computed on the host after its last checkout or reset, so a
    rollback stamps the commit it rolled back to, not the one that failed."""
    lines = [line.strip() for line in run.splitlines()]
    ups = [i for i, line in enumerate(lines) if COMPOSE_UP_RE.search(line)]
    assert ups, f"{job} / {step_name}: no compose up found"

    for up in ups:
        before = lines[:up]
        assigns = [i for i, line in enumerate(before) if ASSIGN_RE.match(line)]
        exports = [i for i, line in enumerate(before) if line == EXPORT_LINE]
        assert assigns, (
            f"{job} / {step_name}: no `{SHA_VAR}=\\$(git rev-parse --short HEAD)` "
            "before its compose up"
        )
        assert (
            exports and exports[-1] > assigns[-1]
        ), f"{job} / {step_name}: `{EXPORT_LINE}` does not follow the assignment"
        checkouts = [i for i, line in enumerate(before) if CHECKOUT_RE.search(line)]
        assert not checkouts or assigns[-1] > checkouts[-1], (
            f"{job} / {step_name}: the SHA is taken before the last git "
            "checkout/reset, so it names a different commit than the one built"
        )


class TestShaExportBehaviour:
    """Run each step's lines from the SHA assignment through its compose up, as
    the host's shell would see them, against a stub docker. The shape tests pin
    what the YAML says; these pin what compose actually receives."""

    @staticmethod
    def _snippet(run: str) -> str:
        lines = run.splitlines()
        assign = next(
            i for i, line in enumerate(lines) if ASSIGN_RE.match(line.strip())
        )
        up = next(
            i for i in range(assign, len(lines)) if COMPOSE_UP_RE.search(lines[i])
        )
        body = "\n".join(lines[assign : up + 1]).rstrip().removesuffix("\\")
        # Unescape the ssh payload's `\$`/`\"`, exactly as the remote shell sees it.
        body = body.replace('\\"', '"').replace("\\$", "$")
        assert "${{" not in body, f"unsubstituted GitHub expression: {body!r}"
        return f"set -euo pipefail\n{body}\n"

    @staticmethod
    def _fake_docker(tmp_path: Path) -> Path:
        import stat as _stat

        fake_bin = tmp_path / "fakebin"
        fake_bin.mkdir()
        docker = fake_bin / "docker"
        docker.write_text(
            "#!/usr/bin/env bash\n"
            'printf "%s" "${BLOOM_WEB_BUILD_SHA-<unset>}" > "$SHA_SEEN"\n'
        )
        docker.chmod(docker.stat().st_mode | _stat.S_IEXEC)
        return fake_bin

    def _run(self, tmp_path: Path, run: str, cwd: Path):
        import os
        import subprocess

        seen = tmp_path / "sha_seen"
        env = {
            **os.environ,
            "PATH": f"{self._fake_docker(tmp_path)}{os.pathsep}{os.environ['PATH']}",
            "SHA_SEEN": str(seen),
            # Stop git finding the repo this test runs in from a non-repo cwd.
            "GIT_CEILING_DIRECTORIES": str(cwd.parent),
        }
        env.pop(SHA_VAR, None)
        result = subprocess.run(
            [_BASH, "-c", self._snippet(run)],
            cwd=cwd,
            capture_output=True,
            text=True,
            env=env,
            timeout=30,
        )
        return result, (seen.read_text(encoding="utf-8") if seen.exists() else None)

    @pytest.mark.parametrize(
        "job,step_name,run",
        _compose_up_steps(),
        ids=[f"{job}:{name}" for job, name, _ in _compose_up_steps()],
    )
    def test_compose_receives_the_hosts_short_sha(self, tmp_path, job, step_name, run):
        import subprocess

        repo = tmp_path / "checkout"
        repo.mkdir()
        git = ["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t"]
        subprocess.run([*git, "init", "-q"], check=True)
        subprocess.run([*git, "commit", "-q", "--allow-empty", "-m", "x"], check=True)
        expected = subprocess.run(
            [*git, "rev-parse", "--short", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()

        result, seen = self._run(tmp_path, run, repo)

        assert result.returncode == 0, result.stderr
        assert seen == expected, f"{job} / {step_name}: compose saw {seen!r}"

    @pytest.mark.parametrize(
        "job,step_name,run",
        _compose_up_steps(),
        ids=[f"{job}:{name}" for job, name, _ in _compose_up_steps()],
    )
    def test_a_failing_git_stops_before_compose(self, tmp_path, job, step_name, run):
        """Outside a checkout, git fails; compose must not run with an empty SHA."""
        not_a_repo = tmp_path / "not-a-checkout"
        not_a_repo.mkdir()

        result, seen = self._run(tmp_path, run, not_a_repo)

        assert result.returncode != 0, f"{job} / {step_name}: continued past git"
        assert seen is None, f"{job} / {step_name}: compose ran with {seen!r}"


def test_the_sha_export_is_never_combined():
    """`export X=$(cmd)` hides cmd's failure from `set -e`; with git failing, the
    empty value would override `unknown` and stamp a bare `1.0.0`."""
    text = DEPLOY_YML.read_text(encoding="utf-8")

    assert f"export {SHA_VAR}=" not in text
