"""Docker-side plumbing: locating the deploy's containers and running rclone.

Everything here talks to the local Docker daemon. Container and network
names are discovered from Compose labels rather than string-built, so a
project rename or a Compose numbering change surfaces as a clear error
instead of a silent connection to nothing.
"""

from __future__ import annotations

import logging
import secrets
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from rclone_rc import redact

logger = logging.getLogger(__name__)

RCLONE_IMAGE = "rclone/rclone:1.71.4"
RC_CONTAINER_PREFIX = "bloom-box-backup-rclone"

# Where the host's state dir appears inside the daemon container, so the run
# report can be uploaded through the same authenticated Box connection.
STATE_MOUNT = "/state"

# Rows psql pulls per cursor fetch. Bounds the db container's memory during
# the manifest read regardless of how many objects the deploy holds.
FETCH_COUNT = 10_000

# The rclone daemon shares the host with the whole Bloom stack, so it gets a
# hard ceiling rather than whatever it decides to take. Its own transfer
# buffers are the bulk of it, and they scale with --transfers.
RC_MEMORY_LIMIT = "512m"
DB_SERVICE = "db-prod"
COMPOSE_PROJECT_LABEL = "com.docker.compose.project"
COMPOSE_SERVICE_LABEL = "com.docker.compose.service"
COMPOSE_NETWORK_LABEL = "com.docker.compose.network"


class DockerError(Exception):
    """A docker command failed, or found nothing where something was required."""


def which(name: str) -> str:
    found = shutil.which(name)
    if not found:
        raise DockerError(f"required binary not on PATH: {name}")
    return found


def run(cmd: list[str], *, input_text: str | None = None, check: bool = True) -> str:
    result = subprocess.run(
        cmd, input=input_text, capture_output=True, text=True
    )
    if check and result.returncode != 0:
        stderr = result.stderr.strip() or "(no stderr)"
        raise DockerError(f"command failed ({result.returncode}): {' '.join(cmd[:3])}…: {stderr}")
    return result.stdout


def project_name(env_name: str) -> str:
    return f"bloom_v2_{env_name}"


def find_container(project: str, service: str) -> str:
    """Container ID for a Compose service, by label. Errors if not exactly one."""
    out = run(
        [
            which("docker"), "ps", "--quiet",
            "--filter", f"label={COMPOSE_PROJECT_LABEL}={project}",
            "--filter", f"label={COMPOSE_SERVICE_LABEL}={service}",
        ]
    )
    ids = [line for line in out.split() if line]
    if not ids:
        raise DockerError(
            f"no running container for service '{service}' in project '{project}' — "
            "is the deploy up, and is --env correct?"
        )
    if len(ids) > 1:
        raise DockerError(f"expected 1 container for '{service}', found {len(ids)}")
    return ids[0]


def find_network(project: str, network: str = "supanet") -> str:
    """Full network name for a Compose network, by label."""
    out = run(
        [
            which("docker"), "network", "ls", "--format", "{{.Name}}",
            "--filter", f"label={COMPOSE_PROJECT_LABEL}={project}",
            "--filter", f"label={COMPOSE_NETWORK_LABEL}={network}",
        ]
    )
    names = [line for line in out.split() if line]
    if not names:
        raise DockerError(
            f"no '{network}' network for project '{project}' — is the deploy up?"
        )
    return names[0]


def psql_query_to_file(
    container: str, sql: str, user: str, database: str, destination: Path
) -> int:
    """Run a read-only query in the db container, streaming rows to a file.

    Prod's `storage.objects` has millions of rows, so the result goes
    straight to disk rather than through a string in memory. The SQL arrives
    on stdin so a long bucket list can never hit the argv length limit, and
    the session is pinned read-only so a mistake in query construction
    cannot write.
    """
    cmd = [
        which("docker"), "exec", "-i", container,
        "psql", "-U", user, "-d", database,
        "--no-align", "--tuples-only", "--field-separator", "\t",
        "--quiet", "--no-psqlrc",
        "-v", "ON_ERROR_STOP=1",
        # Without this psql buffers the whole result set in the db container
        # before writing a byte — millions of rows of it, next to Postgres's
        # own memory on a host that runs the entire stack. FETCH_COUNT makes
        # psql read through a cursor and stream, at identical output format.
        "-v", f"FETCH_COUNT={FETCH_COUNT}",
        "-f", "-",
    ]
    preamble = "SET default_transaction_read_only = on;\nSET statement_timeout = '60min';\n"
    rows = 0
    with destination.open("w", encoding="utf-8") as out:
        process = subprocess.Popen(
            cmd, stdin=subprocess.PIPE, stdout=out, stderr=subprocess.PIPE, text=True
        )
        _, stderr = process.communicate(input=preamble + sql + ";\n")
        if process.returncode != 0:
            raise DockerError(
                f"psql failed ({process.returncode}): {stderr.strip() or '(no stderr)'}"
            )
    with destination.open(encoding="utf-8") as handle:
        rows = sum(1 for line in handle if line.strip())
    return rows


def database_now(container: str, user: str, database: str) -> str:
    """The DATABASE's clock, in the format the manifest reports updated_at in.

    The watermark is compared against `storage.objects.updated_at`, which
    Postgres writes. Taking it from the deploy host instead compares two
    clocks: if the host ever runs ahead, objects written inside the skew are
    never enumerated again, silently and permanently. Containers share the
    host kernel clock today, so this is latent rather than active — but the
    comparison should not depend on that staying true.
    """
    out = run(
        [
            which("docker"), "exec", "-i", container,
            "psql", "-U", user, "-d", database,
            "--no-align", "--tuples-only", "--quiet", "--no-psqlrc",
            "-v", "ON_ERROR_STOP=1",
            "-c", "SELECT to_char(now() AT TIME ZONE 'UTC', "
                  "'YYYY-MM-DD\"T\"HH24:MI:SSOF')",
        ]
    ).strip()
    if not out:
        raise DockerError("could not read the database clock for the watermark")
    return out.splitlines()[0].strip()


@dataclass
class RcDaemon:
    """A short-lived `rclone rcd` container, alive for one backup run."""

    container: str
    url: str
    user: str
    password: str

    def stop(self) -> None:
        try:
            run([which("docker"), "rm", "--force", self.container], check=False)
        except DockerError as exc:  # pragma: no cover - teardown must not mask errors
            logger.warning("could not remove rclone container: %s", exc)


def start_rc_daemon(
    network: str,
    rclone_config: str,
    port: int,
    transfers: int,
    bwlimit: str = "",
    state_dir: str | None = None,
) -> RcDaemon:
    """Start the rclone daemon on the deploy network, bound to loopback.

    The config file is mounted read-only: it holds the Box OAuth token, and
    a run that refreshes the token in a throwaway container copy would lose
    the refresh. Box tokens are long-lived, so the tradeoff is a token the
    operator refreshes with `rclone config reconnect box:` if it ever
    expires — not silent, and not a credential the container can rewrite.
    """
    name = f"{RC_CONTAINER_PREFIX}-{secrets.token_hex(4)}"
    user = "bloom"
    password = secrets.token_urlsafe(24)
    cmd = [
        which("docker"), "run", "--detach", "--name", name,
        "--network", network,
        "--publish", f"127.0.0.1:{port}:{port}",
        "--memory", RC_MEMORY_LIMIT,
        "--volume", f"{rclone_config}:/config/rclone/rclone.conf:ro",
        "--user", f"{_host_uid()}:{_host_gid()}",
        "--env", "RCLONE_CONFIG=/config/rclone/rclone.conf",
    ]
    # Read-only so the daemon can upload the run report the host wrote there.
    # Nothing else in the state dir is read, and nothing is written back.
    if state_dir:
        cmd += ["--volume", f"{state_dir}:{STATE_MOUNT}:ro"]
    cmd += [
        RCLONE_IMAGE,
        "rcd",
        f"--rc-addr=:{port}",
        f"--rc-user={user}",
        f"--rc-pass={password}",
        f"--transfers={transfers}",
        "--retries=1",  # retry/backoff is the caller's job, per object
        "--stats=0",
        # NOTICE, not INFO: rclone echoes the source remote into its own log
        # lines, and ours is a connection string carrying MinIO's root
        # credentials. Per-object progress comes from this job's log instead.
        "--log-level=NOTICE",
    ]
    if bwlimit:
        cmd.append(f"--bwlimit={bwlimit}")
    container = run(cmd).strip()
    logger.info("started rclone daemon container %s on network %s", name, network)
    return RcDaemon(
        container=container,
        url=f"http://127.0.0.1:{port}",
        user=user,
        password=password,
    )


def daemon_logs(container: str, tail: int = 40) -> str:
    result = subprocess.run(
        [which("docker"), "logs", "--tail", str(tail), container],
        capture_output=True,
        text=True,
    )
    return redact((result.stdout + result.stderr).strip())


def _host_uid() -> int:
    import os

    return os.getuid()


def _host_gid() -> int:
    import os

    return os.getgid()
