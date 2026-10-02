"""bloom-web restarts on its own, has a bounded memory, and knows which build it is (bloom#1007).

The trait export (#865) runs inside the bloom-web process and holds its jobs and
finished zips in memory. Without a restart policy, one out-of-memory crash leaves
the whole website down until someone restarts the container by hand. Without a
limit, nothing outside the process bounds it. And each export's sidecar records
`generated_by.version` as package version `+BLOOM_WEB_BUILD_SHA`, read at runtime
(web/app/api/cyl/trait-export/jobs/route.ts, added by #996), so the deploy has to
set that variable or every file just says "1.0.0".

The stamp is the last commit that changed bloom-web's image inputs, not HEAD. It
sits in the container's environment, so a value that changed on every commit
would make compose recreate bloom-web on every deploy, dropping every in-memory
export, even when the image is a cache hit.

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
from tests.unit._workflow_helpers import _logical_lines

# `bash` can resolve to the WSL launcher shim on some Windows dev machines; the
# same lookup as test_deploy_rollback_caddy_reload.py's.
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
MAKEFILE = REPO_ROOT / "Makefile"
DEFAULTS_FILES = (".env.prod.defaults", ".env.staging.defaults")
SERVICE = "bloom-web"
SHA_VAR = "BLOOM_WEB_BUILD_SHA"

# Node 20 sizes its JS heap from the container limit, at about half of it
# (node:20-alpine, measured 2026-10-01: 1536m -> 792 MiB heap, 3g -> 1584 MiB).
# Two running exports plus the Next.js baseline are estimated at ~1 GB of heap,
# and up to MAX_HELD_BYTES of zip buffers sit outside it, inside the same limit.
MEMORY_LIMIT_BYTES = 3 * (1 << 30)

# Everything the bloom-web image is built from, relative to the build context
# (the repo root): the Dockerfile's COPY sources, plus .dockerignore, which
# decides what those sources contain. The Dockerfile itself lives under web/.
IMAGE_INPUTS = ("web", "packages", "package.json", "package-lock.json", ".dockerignore")
SHA_CMD = "git log -1 --format=%H -- " + " ".join(IMAGE_INPUTS)

# The deploy's compose commands run on the host inside `ssh ... "..."`, so the
# SHA must be computed there: `\$(` defers the substitution past the runner's
# shell. Assignment and export are separate because `export X=$(cmd)` returns
# export's status, so a failing git would pass `set -e`; the `test -n` catches a
# git log that matched no commit, which exits 0 with no output. Either way an
# empty value would override `unknown` and stamp a bare `1.0.0`.
ASSIGN_LINE = f"{SHA_VAR}=\\$({SHA_CMD})"
GUARD_LINE = f'test -n \\"\\${SHA_VAR}\\"'
EXPORT_LINE = f"export {SHA_VAR}"
# Any `docker compose ... up`, however its flags are ordered or spelled: every
# one of them can recreate bloom-web, so every one needs the stamp.
COMPOSE_UP_RE = re.compile(r"\bdocker compose\b.*\sup(\s|$)")
UP_TOKEN_RE = re.compile(r"(^|\s)up(\s|$)")
CHECKOUT_RE = re.compile(r"\bgit (reset|checkout|pull)\b")
SET_E_RE = re.compile(r"^set -[a-z]*e[a-z]*$")


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
    """Pinned rather than floored: the compose comment, the PR's sizing and the
    staging check all name this value, and a change should revisit them."""
    service = _service()

    assert "mem_limit" in service, f"{SERVICE} is uncapped"
    assert _bytes(service["mem_limit"]) == MEMORY_LIMIT_BYTES, (
        f"{SERVICE} is capped at {service['mem_limit']!r}; Node sizes its heap at "
        "about half the limit, so a change here changes the export's heap too"
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
    # `build:` goes away when add-ghcr-image-publishing makes prod compose image-only.
    assert SHA_VAR not in (
        (service.get("build") or {}).get("args") or {}
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


def _dockerfile_inputs() -> set[str]:
    """Top-level repo paths the builder stage COPYs from the build context."""
    service = _service()
    dockerfile = REPO_ROOT / service["build"]["dockerfile"]
    found = set()
    for line in dockerfile.read_text(encoding="utf-8").splitlines():
        words = line.split()
        if not words or words[0].upper() not in ("COPY", "ADD"):
            continue
        args = [w for w in words[1:] if not w.startswith("--")]
        if any(w.startswith("--from") for w in words[1:]):
            continue  # from another stage, not the build context
        for source in args[:-1]:
            top = source.split("/", 1)[0]
            matches = sorted(p.name for p in REPO_ROOT.glob(top))
            assert matches, f"COPY source {source!r} matches nothing in the repo"
            found.update(matches)
    return found


def test_the_stamped_paths_are_the_image_inputs():
    """If the Dockerfile starts COPYing something else, a change to it would ship
    under an older stamp."""
    service = _service()
    assert service["build"]["context"] == ".", "paths are relative to the repo root"
    dockerfile_top = service["build"]["dockerfile"].lstrip("./").split("/", 1)[0]

    assert _dockerfile_inputs() == set(IMAGE_INPUTS) - {".dockerignore"}
    assert dockerfile_top in IMAGE_INPUTS, "a Dockerfile change must move the stamp"
    assert (REPO_ROOT / ".dockerignore").exists()


def _compose_up_steps() -> list[tuple[str, str, str]]:
    """(job, step name, run block) for every deploy.yml step with a compose up."""
    jobs = yaml.safe_load(DEPLOY_YML.read_text(encoding="utf-8"))["jobs"]
    found = []
    for job, spec in jobs.items():
        for step in spec.get("steps", []):
            run = step.get("run", "")
            if any(COMPOSE_UP_RE.search(line) for _, line in _logical_lines(run)):
                found.append((job, step["name"], run))
    return found


STEPS = _compose_up_steps()
STEP_IDS = [f"{job}:{name}" for job, name, _ in STEPS]


def test_every_compose_up_is_found():
    """Forward deploy and rollback, in each job. If this changes, the stamp below
    must follow the new call too."""
    assert [(job, name) for job, name, _ in STEPS] == [
        ("deploy-production", "Deploy production stack"),
        ("deploy-production", "Rollback on failure"),
        ("deploy-staging", "Deploy staging stack"),
        ("deploy-staging", "Rollback on failure"),
    ]


def _up_starts(run: str) -> list[int]:
    """0-based physical line index where each compose-up logical line starts."""
    return [
        start - 1 for start, line in _logical_lines(run) if COMPOSE_UP_RE.search(line)
    ]


@pytest.mark.parametrize("job,step_name,run", STEPS, ids=STEP_IDS)
def test_every_compose_up_stamps_the_checked_out_image_inputs(job, step_name, run):
    """Taken on the host after its last checkout or reset, so a rollback stamps the
    code it rolled back to; guarded, and under the step's own `set -e`."""
    lines = [line.strip() for line in run.splitlines()]

    for up in _up_starts(run):
        before = lines[:up]
        where = f"{job} / {step_name}"
        assigns = [i for i, line in enumerate(before) if line == ASSIGN_LINE]
        assert assigns, f"{where}: no `{ASSIGN_LINE}` before its compose up"
        a = assigns[-1]
        assert (
            GUARD_LINE in before[a:]
        ), f"{where}: no `{GUARD_LINE}` after the assignment"
        g = a + before[a:].index(GUARD_LINE)
        assert (
            EXPORT_LINE in before[g:]
        ), f"{where}: `{EXPORT_LINE}` does not follow the guard"

        checkouts = [i for i, line in enumerate(before) if CHECKOUT_RE.search(line)]
        assert not checkouts or a > checkouts[-1], (
            f"{where}: the SHA is taken before the last git checkout/reset, so it "
            "names different code than the one built"
        )
        sets = [i for i, line in enumerate(before[:a]) if SET_E_RE.match(line)]
        assert sets, f"{where}: nothing turns on `set -e` before the assignment"
        assert not any(
            line.startswith("set +") and "e" in line for line in before[sets[0] : a]
        ), f"{where}: `set -e` is turned off before the assignment"


def test_the_sha_export_is_never_combined():
    """`export X=$(cmd)` hides cmd's failure from `set -e`."""
    text = DEPLOY_YML.read_text(encoding="utf-8")

    assert not re.search(rf"\b(export|declare -x)\s+{SHA_VAR}=", text)


def test_the_makefile_stamps_its_prod_compose_up():
    """`make prod-up` / `make staging-up` recreate bloom-web too; unstamped, they
    would drop every export and record `unknown` for the deployed code."""
    ups = [
        line
        for _, line in _logical_lines(MAKEFILE.read_text(encoding="utf-8"))
        if "docker-compose.prod.yml" in line and COMPOSE_UP_RE.search(line)
    ]

    assert len(ups) == 2, f"expected prod-up and staging-up, found {ups}"
    for line in ups:
        assert f"sha=$$({SHA_CMD})" in line, line
        assert 'test -n "$$sha"' in line, line
        assert f"{SHA_VAR}=$$sha docker compose" in line, line


class TestShaExportBehaviour:
    """Run each step's own `set` line and its lines from the SHA assignment through
    its compose up, as the host's shell would see them, against a stub docker. The
    shape tests pin what the YAML says; these pin what compose actually receives."""

    @staticmethod
    def _snippet(run: str) -> str:
        lines = run.splitlines()
        stripped = [line.strip() for line in lines]
        assign = stripped.index(ASSIGN_LINE)
        set_line = next(line for line in stripped[:assign] if SET_E_RE.match(line))
        up = next(u for u in _up_starts(run) if u > assign)
        end = next(i for i in range(up, len(lines)) if UP_TOKEN_RE.search(stripped[i]))
        body = "\n".join(lines[assign : end + 1]).rstrip().removesuffix("\\")
        # Unescape the ssh payload's `\$`/`\"`, exactly as the remote shell sees it.
        body = body.replace('\\"', '"').replace("\\$", "$")
        assert "${{" not in body, f"unsubstituted GitHub expression: {body!r}"
        return f"{set_line}\n{body}\n"

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

    @staticmethod
    def _repo(tmp_path: Path, commits: list[str]) -> tuple[Path, list[str]]:
        """A checkout with one commit per path in `commits`; returns their SHAs."""
        import subprocess

        repo = tmp_path / "checkout"
        repo.mkdir()
        git = ["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t"]
        subprocess.run([*git, "init", "-q"], check=True)
        shas = []
        for i, path in enumerate(commits):
            target = repo / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(f"{i}\n", encoding="utf-8")
            subprocess.run([*git, "add", path], check=True)
            subprocess.run([*git, "commit", "-q", "-m", path], check=True)
            shas.append(
                subprocess.run(
                    [*git, "rev-parse", "HEAD"],
                    check=True,
                    capture_output=True,
                    text=True,
                ).stdout.strip()
            )
        return repo, shas

    @pytest.mark.parametrize("job,step_name,run", STEPS, ids=STEP_IDS)
    def test_compose_receives_the_last_image_input_commit(
        self, tmp_path, job, step_name, run
    ):
        """A later commit that leaves the image alone does not move the stamp, so
        compose does not recreate bloom-web for it."""
        repo, shas = self._repo(tmp_path, ["web/page.ts", "docs/notes.md"])

        result, seen = self._run(tmp_path, run, repo)

        assert result.returncode == 0, result.stderr
        assert seen == shas[0], f"{job} / {step_name}: compose saw {seen!r}"

    @pytest.mark.parametrize("job,step_name,run", STEPS, ids=STEP_IDS)
    def test_no_image_input_commit_stops_before_compose(
        self, tmp_path, job, step_name, run
    ):
        """git log exits 0 with no output when nothing matches; the guard stops it."""
        repo, _ = self._repo(tmp_path, ["docs/notes.md"])

        result, seen = self._run(tmp_path, run, repo)

        assert result.returncode != 0, f"{job} / {step_name}: continued past it"
        assert seen is None, f"{job} / {step_name}: compose ran with {seen!r}"

    @pytest.mark.parametrize("job,step_name,run", STEPS, ids=STEP_IDS)
    def test_a_failing_git_stops_before_compose(self, tmp_path, job, step_name, run):
        """Outside a checkout, git fails; compose must not run with an empty SHA."""
        not_a_repo = tmp_path / "not-a-checkout"
        not_a_repo.mkdir()

        result, seen = self._run(tmp_path, run, not_a_repo)

        assert result.returncode != 0, f"{job} / {step_name}: continued past git"
        assert seen is None, f"{job} / {step_name}: compose ran with {seen!r}"
