#!/usr/bin/env python3
"""Weekly backup of Bloom's Supabase Postgres database to Box.

Runs from the weekly-backup GitHub Actions workflow, which SSHes to the deploy
host and invokes this script there. Dumps the
database and the global role definitions out of the running db-prod container,
verifies both artifacts, and uploads them to Box via a pre-configured rclone
remote.

This job only ever writes to Box. It never deletes there — old backups are kept
until someone removes them by hand.

Exit codes:
  0 = verified backup uploaded
  1 = subprocess failure (docker / pg_dump / gzip / rclone)
  2 = configuration error (missing env, no remote, stack not running)
  3 = an artifact failed verification (missing, undersized, corrupt, or empty)
  4 = the run was killed by SIGTERM or SIGHUP — not a failure of anything it
      ran. Ctrl-C is SIGINT, which is not handled, and exits 130. A cancelled
      or timed-out workflow run kills the ssh client only and this script keeps
      going, so it does not produce this code either. See
      _WIKI/SCHEDULEDJOBS/weekly-backup.md.

See `.env.{staging,prod}.defaults` for the BACKUP_* config surface, and
_WIKI/SCHEDULEDJOBS/weekly-backup.md for setup.
"""

from __future__ import annotations

import argparse
import gzip
import logging
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger("bloom_weekly_backup")

COMPOSE_FILE = "docker-compose.prod.yml"
# Both environments run this one compose file on the same host, told apart only
# by the project name. The file pins the production one, so `-p` is what selects
# a stack: deploy.yml passes `-p bloom_v2_staging` for staging and lets the
# pinned name stand for production. Resolving a container without `-p` therefore
# lands on PRODUCTION whatever --env says, which is the whole reason this
# mapping exists rather than being left implicit.
COMPOSE_PROJECTS = {
    "prod": "bloom_v2_prod",
    "staging": "bloom_v2_staging",
}
# The environment the schedule backs up. Staging is reachable by hand and by
# workflow_dispatch, for rehearsing the whole path end to end.
DEFAULT_ENV = "prod"
# Under the invoking user's own home, so no root-created directory is needed.
DEFAULT_STATE_DIR = "~/.local/state/bloom-weekly-backup"
DB_SERVICE = "db-prod"

# Size floors. An empty or truncated dump is the failure this job exists to
# avoid reporting as success; both floors sit far below any real dump.
MIN_DATABASE_BYTES = 4096
MIN_GLOBALS_BYTES = 256

# Content floors. A dump holding no data at all is well-formed, clears the size
# floor and gzips cleanly, so size alone never catches it. The row count is a
# floor on "did anything come out", not a check that the right database was
# dumped: every applied migration is one row in a bookkeeping table, and there
# are far more of those than this floor, so a migrated-but-empty database clears
# it. Confirming which database was dumped is the compose project mapping's job.
DB_DUMP_COMPLETE_MARKER = "-- PostgreSQL database dump complete"
GLOBALS_DUMP_COMPLETE_MARKER = "-- PostgreSQL database cluster dump complete"
MIN_DATA_ROWS = 100
MIN_ROLE_STATEMENTS = 5

# The only keys this job reads out of a deploy env file. Importing the whole
# file instead hands every child process whatever it happens to contain: rclone
# takes its entire option surface from `RCLONE_*`, so one `RCLONE_CONFIG=` line
# would send a plaintext dump to somebody else's remote, and `LD_PRELOAD` runs
# code as the deploy user. Both files are checked in, so that reach would be
# writable from a config-only change.
ENV_KEYS = (
    "POSTGRES_USER",
    "POSTGRES_PASSWORD",
    "POSTGRES_DB",
    "BACKUP_STATE_DIR",
    "BACKUP_MIN_FREE_BYTES",
    "BACKUP_RCLONE_REMOTE",
    "BACKUP_RCLONE_DEST_DIR",
)

# Free space the working copy needs before a dump starts, whichever volume
# BACKUP_STATE_DIR puts it on. Both layouts have something to lose: the deploy
# hosts point it at the roomy volume the database itself lives on, where filling
# up stops Postgres writing WAL; left unset it falls back under the invoking
# user's home, where filling the root filesystem takes docker, sshd and the
# Actions runner with it. This is not a prediction of the dump's size — it is a
# floor that refuses to run on a volume already too full to hold one. Tune per
# host with BACKUP_MIN_FREE_BYTES.
DEFAULT_MIN_FREE_BYTES = 20 * 1024**3  # 20 GiB, ~3x a current 6-7 GB dump

# How long pg_dump waits for a table lock before giving up. It takes ACCESS
# SHARE on every table, which only DDL conflicts with — and a deploy's
# migrations cannot overlap a backup, both landing on the one shared runner. So
# this is a safety valve rather than something a run is expected to spend. Kept
# short because a waiting pg_dump queues every other reader behind it: the wait
# is a bound on how long the application stalls on that table, and waiting
# longer does not make a lock somebody else holds any more likely to come free.
LOCK_WAIT_TIMEOUT_MS = 60_000

# An unquoted Postgres identifier, which is all a database name may be here:
# the name reaches an artifact filename, and `../` in it would write a
# plaintext dump outside the 0700 state directory, outside the working
# directory's cleanup and outside the sweep's glob — so nothing would ever
# remove it.
DB_NAME_PATTERN = re.compile(r"[A-Za-z_][A-Za-z0-9_$]{0,62}\Z")

EXIT_OK = 0
EXIT_SUBPROCESS = 1
EXIT_CONFIG = 2
EXIT_VERIFY = 3
EXIT_SIGNAL = 4


class ConfigError(RuntimeError):
    """Environment or host is not set up for this job to run."""


class VerificationError(RuntimeError):
    """An artifact was produced but cannot be a usable dump."""

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default)


def load_env_file(path: Path) -> dict[str, str]:
    """Read a deploy .env file into a plain dict.

    A systemd unit would have read this file for us; over SSH nothing does,
    and sourcing it in the
    shell would let a value containing spaces or quotes rewrite the command.

    Deliberately not a shell: `export ` prefixes, inline `#` comments and
    values spanning several lines are all read literally. The deploy env files
    use none of them, and guessing at them would corrupt a value containing a
    `#` far more quietly than refusing to.
    """
    values: dict[str, str] = {}
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if not key or not key[0].isalpha():
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        values[key] = value
    return values


def apply_env_file(path: Path) -> dict[str, str]:
    """Load this job's keys as defaults, and return the ones it found.

    Only ENV_KEYS are taken; the rest of the file is left where it is. A real
    environment variable still wins over the file.

    The password is returned rather than exported: a child started without an
    explicit environment inherits the whole of ours, so a secret left here would
    reach gzip and rclone as well as the dump.

    A blank value counts as absent, so the defaults still apply. Blanking a line
    is how someone asks for the default, and an empty BACKUP_STATE_DIR would
    otherwise resolve to the working directory.
    """
    if not path.is_file():
        raise ConfigError(f"env file not found: {path}")
    try:
        values = load_env_file(path)
    except OSError as exc:
        # The runner writes .env.<env> at mode 600 and the deploy user reads it.
        raise ConfigError(f"cannot read env file {path}: {exc}") from exc
    found: dict[str, str] = {}
    for key in ENV_KEYS:
        value = values.get(key, "")
        if not value:
            continue
        found[key] = value
        if key != "POSTGRES_PASSWORD":
            os.environ.setdefault(key, value)
    logger.info("loaded %d of %d values from %s", len(found), len(values), path.name)
    return found


def _pg_password(values: dict[str, str]) -> str:
    """The database password, which the deploy env file sets as POSTGRES_PASSWORD.

    db-prod authenticates every connection, including one opened from inside
    the container, so a dump without this dies at `fe_sendauth: no password
    supplied`. Absent, that is a configuration error: exit 2 points the operator
    at the env file, which is where the answer is.
    """
    password = _env("POSTGRES_PASSWORD") or values.get("POSTGRES_PASSWORD", "")
    if not password:
        raise ConfigError(
            "POSTGRES_PASSWORD is not set — the deploy env file must define it "
            "for pg_dump to authenticate against " + DB_SERVICE
        )
    return password


def _pg_database() -> str:
    """The database to dump, checked because the name reaches a filename."""
    name = _env("POSTGRES_DB", "postgres")
    if not DB_NAME_PATTERN.match(name):
        raise ConfigError(
            f"POSTGRES_DB is not a plain database name: {name!r} — a name "
            "carrying path separators would put the dump outside the state "
            "directory, where nothing cleans it up"
        )
    return name


def dump_command(container: str, argv: list[str],
                 password: str) -> tuple[list[str], dict[str, str]]:
    """A docker exec of argv, plus the environment it must be run with.

    `-e PGPASSWORD` carries no `=value` on purpose: that form tells docker to
    copy the value out of this process, so the password never lands in the
    host's process list for any user's `ps` to read.
    """
    cmd = [_which("docker"), "exec", "-i", "-e", "PGPASSWORD", container, *argv]
    return cmd, {**os.environ, "PGPASSWORD": password}


def _which(name: str) -> str:
    """Resolve a binary, raising a config error rather than a traceback."""
    found = shutil.which(name)
    if not found:
        raise ConfigError(f"required binary not on PATH: {name}")
    return found

def _run(cmd: list[str], cwd: Path | None = None) -> str:
    """Run a command, log its output to the journal, raise on non-zero exit."""
    logger.info("running: %s", " ".join(cmd))
    result = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    for line in (result.stderr or "").rstrip().splitlines():
        logger.warning("  stderr: %s", line)
    if result.returncode != 0:
        raise subprocess.CalledProcessError(
            result.returncode, cmd, output=result.stdout, stderr=result.stderr
        )
    return result.stdout

def compose_project(env_name: str) -> str:
    """The compose project this environment's stack was brought up under."""
    try:
        return COMPOSE_PROJECTS[env_name]
    except KeyError:
        raise ConfigError(
            f"no compose project known for --env {env_name}; "
            f"known environments: {', '.join(sorted(COMPOSE_PROJECTS))}"
        ) from None


def compose_args(deploy_dir: Path, env_name: str) -> list[str]:
    """The compose invocation this environment's stack was brought up with.

    `-p` is not decoration. Without it compose falls back to the name pinned in
    the file, which is production's — so a staging run would read staging's env
    file and dump the PRODUCTION database into staging's Box folder.
    """
    return [
        "-p", compose_project(env_name),
        "-f", str(deploy_dir / COMPOSE_FILE),
        "--env-file", str(deploy_dir / f".env.{env_name}"),
    ]

def parse_container_id(ps_output: str) -> str:
    """Pick the container id out of `compose ps -q` output.

    Empty output means the stack is not running, which must be a hard error —
    a backup of nothing is the one result worse than no backup at all.
    """
    ids = [line.strip() for line in ps_output.splitlines() if line.strip()]
    if not ids:
        raise ConfigError(
            f"no running '{DB_SERVICE}' container — is the stack up in this deploy directory?"
        )
    if len(ids) > 1:
        raise ConfigError(f"expected one '{DB_SERVICE}' container, found {len(ids)}")
    return ids[0]


def resolve_container(deploy_dir: Path, env_name: str) -> str:
    """Locate the database container through compose.

    Never by name: compose derives its project from the deploy directory, which
    differs per host, so a hardcoded container name is wrong somewhere.
    """
    out = _run(
        [_which("docker"), "compose", *compose_args(deploy_dir, env_name), "ps", "-q", DB_SERVICE],
        cwd=deploy_dir,
    )
    container = parse_container_id(out)
    logger.info("resolved %s container: %s", DB_SERVICE, container[:12])
    return container

# ---------------------------------------------------------------------------
# Dump + verify
# ---------------------------------------------------------------------------

def _terminate(signum: int, _frame: object) -> None:
    """Turn a kill signal into an exception so the working dir unwinds.

    Its own exit code: a SystemExit carrying a string exits 1, which would be
    indistinguishable from a pg_dump or rclone failure. Being cancelled is not
    a failure of anything this job ran.
    """
    logger.error("terminated by signal %d", signum)
    raise SystemExit(EXIT_SIGNAL)


def _state_dir() -> Path:
    """Where working copies live. BACKUP_STATE_DIR is read from the env file."""
    return Path(_env("BACKUP_STATE_DIR", DEFAULT_STATE_DIR)).expanduser()


def _min_free_bytes() -> int:
    """The free-space floor for this host, or the default if none is set."""
    raw = _env("BACKUP_MIN_FREE_BYTES", "")
    if not raw:
        return DEFAULT_MIN_FREE_BYTES
    try:
        floor = int(raw)
    except ValueError:
        raise ConfigError(
            f"BACKUP_MIN_FREE_BYTES is not a number of bytes: {raw!r}"
        ) from None
    if floor <= 0:
        raise ConfigError(
            f"BACKUP_MIN_FREE_BYTES must be positive, not {floor} — a run with "
            "no floor can fill the filesystem the database is running on"
        )
    return floor


def verify_free_space(state_dir: Path) -> int:
    """Refuse to dump onto a volume too full to hold one. Returns free bytes.

    A dump that runs a volume out of space takes more than itself down with it,
    so this is the difference between a run that fails on its own and a run
    that fails on something else's behalf.
    """
    free = shutil.disk_usage(state_dir).free
    floor = _min_free_bytes()
    if free < floor:
        raise ConfigError(
            f"{free:,} bytes free on {state_dir}, below the {floor:,}-byte "
            "floor — a dump large enough to fill this volume would take more "
            "than this backup down with it"
        )
    logger.info("%s has %s bytes free", state_dir, f"{free:,}")
    return free


def sweep_best_effort(env_file: Path) -> int:
    """Sweep on a path that is already failing, without adding a new failure.

    A run that dies in config loading returns before the sweep below, so a
    persistent config error would let an orphaned plaintext dump — `auth.users`
    and all — outlive the one-week bound this job promises.

    The working directory is read out of the env file here rather than out of
    the environment, because the failures this runs on are exactly the ones
    where that file was never loaded — leaving `_state_dir` to fall back to a
    default that has nothing to do with this deployment. If the file cannot be
    read, nothing is swept: a directory we had to guess at is not one to delete
    from.
    """
    configured = _env("BACKUP_STATE_DIR")
    if not configured:
        try:
            configured = load_env_file(env_file).get("BACKUP_STATE_DIR", "") \
                if env_file.is_file() else ""
        except OSError as exc:
            logger.warning("cannot read %s, so leaving any working directory "
                           "in place: %s", env_file, exc)
            return 0
    if not configured:
        logger.warning("no BACKUP_STATE_DIR in %s, so leaving any working "
                       "directory in place", env_file)
        return 0
    try:
        state_dir = Path(configured).expanduser()
        return sweep_stale_work_dirs(state_dir) if state_dir.is_dir() else 0
    except OSError as exc:
        logger.warning("could not sweep stale working directories: %s", exc)
        return 0


def sweep_stale_work_dirs(state_dir: Path) -> int:
    """Remove working directories left by a run killed before its cleanup ran.

    Deliberately narrow: only entries directly inside state_dir, and only ones
    named with this job's own prefix. Files and symlinks are skipped — rmtree
    refuses both anyway, so this is about not reporting them as leftovers it
    failed to remove. Returns how many were actually removed, not how many were
    found.
    """
    removed = 0
    stuck: list[str] = []
    for leftover in state_dir.glob("bloom-backup-*"):
        if leftover.is_symlink() or not leftover.is_dir():
            continue
        shutil.rmtree(leftover, ignore_errors=True)
        if leftover.exists():
            stuck.append(leftover.name)
        else:
            removed += 1
    if removed:
        logger.warning("removed %d working dir(s) left by an interrupted run",
                       removed)
    for name in stuck:
        logger.error("could not remove %s — it may still hold a plaintext dump",
                     name)
    return removed


def _stream_to_gzip(cmd: list[str], out: Path, env: dict[str, str] | None = None) -> None:
    """Run cmd, pipe it through gzip into out, and check BOTH exit statuses.

    A shell pipeline reports only the last process, which is how a truncated
    dump wrapped in valid gzip passes for a good backup.

    env goes to cmd alone; gzip has no business holding the password.
    """
    # Opened 0600 rather than chmod'ed afterwards: this file holds plaintext
    # auth.users for the whole dump, so a mode fixed only once the dump finishes
    # would be right for none of the window that matters. Owner-only here means
    # the guarantee does not rest on the host's umask or its inherited ACLs.
    fd = os.open(out, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with open(fd, "wb") as handle:
        gzip_proc = subprocess.Popen(
            [_which("gzip"), "-c"], stdin=subprocess.PIPE, stdout=handle
        )
        src_proc = subprocess.Popen(
            cmd, stdout=gzip_proc.stdin, stderr=subprocess.PIPE, env=env
        )
        gzip_proc.stdin.close()  # type: ignore[union-attr]
        _, src_err = src_proc.communicate()
        gzip_proc.wait()
    for line in (src_err or b"").decode(errors="replace").rstrip().splitlines():
        logger.warning("  stderr: %s", line)
    if gzip_proc.returncode != 0:
        # Checked before the source's status on purpose. When the volume fills,
        # gzip is what fails first, and the SIGPIPE it sends upstream makes the
        # source exit non-zero too — so checking the source first reports a full
        # disk as "pg_dump died". The other direction still works: a source that
        # fails closes its stdout, gzip sees EOF and exits 0.
        logger.error("gzip failed writing %s — %s bytes free on %s", out.name,
                     f"{shutil.disk_usage(out.parent).free:,}", out.parent)
        raise subprocess.CalledProcessError(gzip_proc.returncode, ["gzip"])
    if src_proc.returncode != 0:
        raise subprocess.CalledProcessError(src_proc.returncode, cmd, stderr=src_err)


def verify_artifact(path: Path, min_bytes: int) -> int:
    """Reject an artifact that cannot be a real dump. Returns its size."""
    if not path.exists():
        raise VerificationError(f"expected artifact was never written: {path.name}")
    size = path.stat().st_size
    if size < min_bytes:
        raise VerificationError(
            f"{path.name} is {size} bytes, below the {min_bytes}-byte floor — "
            "treating as a failed dump rather than uploading it"
        )
    try:
        _run([_which("gzip"), "-t", str(path)])
    except subprocess.CalledProcessError as exc:
        # Same symptom as the checks above; reporting it as a subprocess
        # failure would send the operator looking at docker instead.
        raise VerificationError(f"{path.name} failed its gzip integrity check") from exc
    logger.info("verified %s: %d bytes", path.name, size)
    return size


def scan_plain_dump(path: Path, marker: str) -> tuple[int, int, bool]:
    """Read a gzipped plain dump once: data rows, CREATE ROLEs, did it finish.

    pg_dump writes its completion line last, so seeing that line — outside any
    COPY block, where a data row could otherwise forge it — is what proves the
    dump ran to the end rather than stopping partway.
    """
    rows = 0
    roles = 0
    completed = False
    in_copy = False
    with gzip.open(path, "rt", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            if in_copy:
                if line.startswith("\\."):
                    in_copy = False
                else:
                    rows += 1
                continue
            if line.startswith("COPY ") and line.rstrip().endswith("FROM stdin;"):
                in_copy = True
            elif line.startswith("CREATE ROLE "):
                roles += 1
            elif line.startswith(marker):
                completed = True
    return rows, roles, completed


def verify_database_content(path: Path) -> int:
    """Reject a database dump that finished but carries no data at all.

    The size floor and `gzip -t` both pass on a dump whose every table came out
    empty. Returns the number of data rows seen — bookkeeping rows included, so
    this does not distinguish a full database from a freshly migrated one.
    """
    rows, _, completed = scan_plain_dump(path, DB_DUMP_COMPLETE_MARKER)
    if not completed:
        raise VerificationError(
            f"{path.name} never reached its '{DB_DUMP_COMPLETE_MARKER}' line — "
            "the dump stopped partway"
        )
    if rows < MIN_DATA_ROWS:
        raise VerificationError(
            f"{path.name} holds {rows} data row(s), below the {MIN_DATA_ROWS}-row "
            "floor — even a database with no records of its own carries more "
            "than this, so check POSTGRES_DB and the resolved container"
        )
    logger.info("verified %s content: %d data row(s)", path.name, rows)
    return rows


def verify_globals_content(path: Path) -> int:
    """Reject a globals dump with no roles in it. Returns the role count.

    The database dump's OWNER and GRANT statements name these roles; a globals
    artifact without them restores to a cluster that cannot own its own data.
    """
    _, roles, completed = scan_plain_dump(path, GLOBALS_DUMP_COMPLETE_MARKER)
    if not completed:
        raise VerificationError(
            f"{path.name} never reached its '{GLOBALS_DUMP_COMPLETE_MARKER}' line — "
            "the dump stopped partway"
        )
    if roles < MIN_ROLE_STATEMENTS:
        raise VerificationError(
            f"{path.name} defines {roles} role(s), below the {MIN_ROLE_STATEMENTS} "
            "this cluster always has — the database dump's OWNER and GRANT "
            "statements would have nothing to bind to"
        )
    logger.info("verified %s content: %d role(s)", path.name, roles)
    return roles


def dump_database(container: str, work_dir: Path, timestamp: str,
                  password: str) -> Path:
    """Dump the whole database, keeping owners and privileges."""
    pg_user = _env("POSTGRES_USER", "supabase_admin")
    pg_db = _pg_database()
    out = work_dir / f"postgres-{pg_db}-{timestamp}.sql.gz"
    logger.info("dumping database %s -> %s", pg_db, out.name)
    cmd, env = dump_command(
        container,
        ["pg_dump", "-U", pg_user, "-d", pg_db, "--format=plain",
         f"--lock-wait-timeout={LOCK_WAIT_TIMEOUT_MS}"],
        password,
    )
    _stream_to_gzip(cmd, out, env=env)
    verify_artifact(out, MIN_DATABASE_BYTES)
    verify_database_content(out)
    return out


def dump_globals(container: str, work_dir: Path, timestamp: str,
                 password: str) -> Path:
    """Dump the roles the database dump's OWNER/GRANT statements reference."""
    pg_user = _env("POSTGRES_USER", "supabase_admin")
    out = work_dir / f"globals-{timestamp}.sql.gz"
    logger.info("dumping globals -> %s", out.name)
    cmd, env = dump_command(
        container, ["pg_dumpall", "-U", pg_user, "--globals-only"], password
    )
    _stream_to_gzip(cmd, out, env=env)
    verify_artifact(out, MIN_GLOBALS_BYTES)
    verify_globals_content(out)
    return out


# ---------------------------------------------------------------------------
# Upload
# ---------------------------------------------------------------------------


def format_summary(env_name: str, timestamp: str, artifacts: list[Path],
                   destination: str, uploaded: bool) -> str:
    """A short human-readable record of the run, for the weekly glance."""
    lines = [
        f"env: {env_name}",
        f"run: {timestamp}",
        f"destination: {destination}" if uploaded else "destination: (dry run — not uploaded)",
        "artifacts:",
    ]
    for artifact in artifacts:
        size = artifact.stat().st_size if artifact.exists() else 0
        lines.append(f"  {artifact.name}  {size:,} bytes")
    return "\n".join(lines)


# Retry the upload rather than lose a verified dump to a blip; rclone's own
# backoff, so nothing here re-implements one.
RCLONE_RETRY_ARGS = ["--retries", "5", "--retries-sleep", "30s",
                     "--low-level-retries", "20"]


def backup_destination(env_name: str) -> tuple[str, str]:
    """The remote and directory this environment's artifacts belong in."""
    remote = _env("BACKUP_RCLONE_REMOTE", "")
    if not remote:
        raise ConfigError("BACKUP_RCLONE_REMOTE is not set")
    return remote, _env("BACKUP_RCLONE_DEST_DIR", f"bloom-backups/{env_name}")


def upload(artifacts: list[Path], env_name: str, timestamp: str) -> str:
    """Push this run's artifacts to Box under one folder. Returns that folder.

    One copy of the working directory rather than one per file: a run that
    fails halfway then leaves an obviously incomplete folder, not a database
    dump sitting among good backups with no globals beside it.
    """
    remote, dest_dir = backup_destination(env_name)
    work_dirs = {artifact.parent for artifact in artifacts}
    if len(work_dirs) != 1:
        raise ConfigError(f"artifacts span {len(work_dirs)} directories, expected one")
    destination = f"{remote}:{dest_dir}/{timestamp}/"
    logger.info("uploading %d artifact(s) to %s", len(artifacts), destination)
    # A transient blip on the way to Box would otherwise discard a dump that is
    # already taken and verified, and the next attempt is a week away.
    _run([_which("rclone"), "copy", str(work_dirs.pop()), destination, *RCLONE_RETRY_ARGS])
    return destination


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Weekly Postgres backup to Box.")
    # Choices come from the project map so the two cannot drift: an environment
    # this script will accept is one it knows how to resolve a container for.
    parser.add_argument("--env", required=True, choices=sorted(COMPOSE_PROJECTS),
                        help="Which deploy environment to back up.")
    parser.add_argument("--deploy-dir", required=True, type=Path,
                        help="Deploy directory holding the compose file and env file.")
    parser.add_argument("--env-file", type=Path, default=None,
                        help="Env file to read config from (default: <deploy-dir>/.env.<env>).")
    parser.add_argument("--dry-run", action="store_true",
                        help="Dump and verify, but skip the upload.")
    parser.add_argument("--print-destination", action="store_true",
                        help="Print this environment's Box destination and exit.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s"
    )

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    logger.info("starting bloom-weekly-backup env=%s timestamp=%s", args.env, timestamp)

    try:
        # Fail here rather than at `compose ps`: an unknown --env would otherwise
        # fall through to the project name pinned in the compose file, which is
        # production's, and dump production under another environment's name.
        project = compose_project(args.env)
        logger.info("targeting compose project %s", project)
        deploy_dir = args.deploy_dir.resolve()
        if not deploy_dir.is_dir():
            raise ConfigError(f"deploy directory does not exist: {deploy_dir}")
        env_values = apply_env_file(args.env_file or deploy_dir / f".env.{args.env}")
        if args.print_destination:
            # So the workflow never re-implements this parsing in shell.
            remote, dest_dir = backup_destination(args.env)
            print(f"{remote}:{dest_dir}")
            return EXIT_OK
        state_dir = _state_dir()
        # The working copy holds a full dump; keep it off other users. mode= on
        # the create leaves no window between the two calls; the chmod is what
        # tightens a directory an earlier run left looser.
        # Created by hand as part of host setup, never here. A job that makes
        # the path it is given turns a typo in BACKUP_STATE_DIR into a fresh
        # directory with a full plaintext dump in it, somewhere nobody watches.
        if not state_dir.is_dir():
            raise ConfigError(
                f"{state_dir} does not exist — the working directory is part of "
                "host setup, or BACKUP_STATE_DIR is wrong"
            )
        # It persists between runs, so a directory left loose has to be fixed
        # rather than trusted: it holds a full plaintext dump.
        try:
            state_dir.chmod(0o700)
        except OSError as exc:
            raise ConfigError(
                f"cannot use {state_dir} as the working directory: {exc}"
            ) from exc
        # Cancelling the job or hitting timeout-minutes kills this process from
        # outside; without a handler the working dir below is never unwound.
        signal.signal(signal.SIGTERM, _terminate)
        signal.signal(signal.SIGHUP, _terminate)
        # SIGKILL and power loss cannot be caught, so sweep what they left.
        # Before the space check, so a run is not refused over room an orphaned
        # dump is holding.
        sweep_stale_work_dirs(state_dir)
        # Before the dump, not during it: a volume this run would fill is the
        # one the database is writing WAL to.
        verify_free_space(state_dir)
        # Same reasoning as the destination check below: these are config, and
        # a run that finds them wrong has already spent the dump window.
        password = _pg_password(env_values)
        _pg_database()
        # Resolve the destination before dumping: finding out afterwards costs
        # the whole dump window and discards the artifact.
        if not args.dry_run:
            _which("rclone")
            backup_destination(args.env)
    except ConfigError as exc:
        logger.error("configuration error: %s", exc)
        # This run is over, but a dump orphaned by an earlier SIGKILL must not
        # outlive it just because the config is broken this week too.
        sweep_best_effort(args.env_file or args.deploy_dir / f".env.{args.env}")
        return EXIT_CONFIG

    # The working copy holds a full dump. The context manager covers returns
    # and exceptions; signals are covered by the handlers installed above.
    with tempfile.TemporaryDirectory(prefix="bloom-backup-", dir=str(state_dir)) as tmp:
        work_dir = Path(tmp)
        try:
            container = resolve_container(deploy_dir, args.env)
            artifacts = [
                dump_database(container, work_dir, timestamp, password),
                dump_globals(container, work_dir, timestamp, password),
            ]
        except VerificationError as exc:
            logger.error("verification failed: %s", exc)
            return EXIT_VERIFY
        except ConfigError as exc:
            logger.error("configuration error: %s", exc)
            return EXIT_CONFIG
        except subprocess.CalledProcessError as exc:
            logger.error("dump failed: %s", exc)
            return EXIT_SUBPROCESS

        if args.dry_run:
            logger.info("DRY RUN — %d artifact(s) verified, skipping upload",
                        len(artifacts))
            print(format_summary(args.env, timestamp, artifacts, "", uploaded=False))
            return EXIT_OK

        try:
            destination = upload(artifacts, args.env, timestamp)
        except ConfigError as exc:
            logger.error("configuration error: %s", exc)
            return EXIT_CONFIG
        except subprocess.CalledProcessError as exc:
            logger.error("upload failed: %s", exc)
            return EXIT_SUBPROCESS

        print(format_summary(args.env, timestamp, artifacts,
                             destination, uploaded=True))

    logger.info("bloom-weekly-backup env=%s timestamp=%s complete", args.env, timestamp)
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
