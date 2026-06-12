"""Helpers for monitor.py — parsing, classification, email.

Pure functions wherever possible so monitor_test.py can exercise them
against the fixture .jsonl files in test_fixtures/.
"""

from __future__ import annotations

import json
import logging
import smtplib
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from pathlib import Path
from typing import Iterable

logger = logging.getLogger(__name__)

LOG_WINDOW_HOURS = 168  # 7 days
SILENT_EXPIRY_GRACE_DAYS = 7  # selected_time + this = silent-expiry threshold

# Grounded against real Caddy 2.11.3 log strings — see fixtures dir.
SUCCESS_LOGGER = "tls.obtain"
SUCCESS_MSG = "certificate obtained successfully"
PLANNING_LOGGER = "tls.cache.maintenance"
PLANNING_MSG = "updated and stored ACME renewal information"
FAILURE_MARKERS = [
    ("tls.obtain", "could not get certificate from issuer"),
    ("tls", "job failed"),
    ("http.acme_client", "cleaning up solver"),
]
RETRY_LOGGER = "tls.obtain"
RETRY_MSG = "will retry"

# Prod issues apex + wildcard. Staging issues wildcard only.
EXPECTED_SUCCESS_PER_ENV = {"staging": 1, "prod": 2}

# Concrete identifiers we expect at each env. Used by _partial_success to name
# which cert is missing (not just the count). Staging's wildcard covers all
# three staging hostnames; prod issues both apex + wildcard.
EXPECTED_IDENTIFIERS_PER_ENV = {
    "staging": ["*.bloom-dev.salk.edu"],
    "prod": ["bloom-dev.salk.edu", "*.bloom-dev.salk.edu"],
}


@dataclass
class CaddyEnv:
    label: str
    container: str
    cert_dir: str


@dataclass
class CertInfo:
    subject: str
    issuer: str
    not_before: datetime
    not_after: datetime


@dataclass
class CaddyEvent:
    ts: float
    level: str
    logger_path: str
    msg: str
    identifier: str | None = None
    issuer: str | None = None
    error: str | None = None
    selected_time: float | None = None
    attempt: int | None = None
    elapsed: float | None = None

    def is_success(self) -> bool:
        return self.logger_path == SUCCESS_LOGGER and self.msg == SUCCESS_MSG

    def is_failure(self) -> bool:
        return self.level == "error" and (self.logger_path, self.msg) in FAILURE_MARKERS

    def is_retry(self) -> bool:
        return self.logger_path == RETRY_LOGGER and self.msg == RETRY_MSG

    def is_planning(self) -> bool:
        return self.logger_path == PLANNING_LOGGER and self.msg == PLANNING_MSG


@dataclass
class MonitorState:
    last_run_utc: str | None = None
    last_not_before: dict[str, str] = field(default_factory=dict)


@dataclass
class Notification:
    env: str
    kind: str  # renewed | failed | silent_expiry | partial_success | caddy_unreachable | preflight
    subject_suffix: str
    body: str


def check_container_running(env: CaddyEnv) -> tuple[bool, str | None]:
    """Returns (True, None) if the Caddy container is reachable via `docker exec`.
    Otherwise returns (False, error_message) so the caller can build a
    `caddy_unreachable` notification instead of false-alarming as a renewal failure.

    Catches: container stopped, container missing, docker daemon unreachable, any
    other reason `docker exec` can't enter the container — anything that means
    "we can't observe the cert this run."
    """
    result = subprocess.run(
        ["docker", "exec", env.container, "true"],
        capture_output=True, text=True, check=False,
    )
    if result.returncode == 0:
        return True, None
    error_msg = result.stderr.strip() or result.stdout.strip() or f"docker exec returned rc={result.returncode}"
    return False, error_msg


def run_subprocess(cmd: list[str]) -> str:
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise RuntimeError(
            f"subprocess failed (rc={result.returncode}): {' '.join(cmd)}\n"
            f"stderr: {result.stderr}"
        )
    return result.stdout


def list_cert_subjects(env: CaddyEnv) -> list[str]:
    out = run_subprocess(["docker", "exec", "-T", env.container, "ls", env.cert_dir])
    return [line.strip() for line in out.splitlines() if line.strip()]


def read_cert(env: CaddyEnv, subject_dir: str) -> CertInfo:
    crt_path = f"{env.cert_dir}/{subject_dir}/{subject_dir}.crt"
    out = run_subprocess([
        "docker", "exec", "-T", env.container,
        "openssl", "x509", "-in", crt_path, "-noout", "-subject", "-issuer", "-dates",
    ])
    return parse_openssl_output(out)


def parse_openssl_output(out: str) -> CertInfo:
    fields = {}
    for line in out.splitlines():
        if "=" in line:
            key, _, value = line.partition("=")
            fields[key.strip()] = value.strip()
    return CertInfo(
        subject=fields["subject"],
        issuer=fields["issuer"],
        not_before=parse_openssl_date(fields["notBefore"]),
        not_after=parse_openssl_date(fields["notAfter"]),
    )


def parse_openssl_date(s: str) -> datetime:
    return datetime.strptime(s, "%b %d %H:%M:%S %Y %Z").replace(tzinfo=timezone.utc)


def read_caddy_logs(env: CaddyEnv) -> str:
    return run_subprocess([
        "docker", "logs", "--since", f"{LOG_WINDOW_HOURS}h", env.container,
    ])


def parse_events(blob: str) -> list[CaddyEvent]:
    events = []
    for line in blob.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            continue
        events.append(CaddyEvent(
            ts=data.get("ts", 0.0),
            level=data.get("level", ""),
            logger_path=data.get("logger", ""),
            msg=data.get("msg", ""),
            identifier=data.get("identifier") or _first(data.get("identifiers")),
            issuer=data.get("issuer"),
            error=data.get("error"),
            selected_time=data.get("selected_time"),
            attempt=data.get("attempt"),
            elapsed=data.get("elapsed"),
        ))
    return events


def _first(maybe_list):
    return maybe_list[0] if isinstance(maybe_list, list) and maybe_list else None


def latest_planning_for(identifier: str, events: Iterable[CaddyEvent]) -> CaddyEvent | None:
    candidates = [e for e in events if e.is_planning() and e.identifier == identifier]
    return max(candidates, key=lambda e: e.ts, default=None)


def subject_to_identifier(subject: str) -> str:
    for part in subject.replace(" ", "").split(","):
        if part.startswith("CN="):
            return part[3:]
    return subject


def load_state(path: Path) -> MonitorState:
    """Read the state file, or return empty state if absent OR unreadable.

    A corrupt or unreadable state file is moved aside with a
    `.corrupt-<unix-ts>` suffix and a warning is logged. The monitor then
    re-baselines (next save_state writes a fresh file). This prevents a
    one-time file corruption from wedging every future weekly run.
    """
    if not path.exists():
        return MonitorState()
    try:
        data = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError) as exc:
        corrupt_path = path.parent / f"{path.name}.corrupt-{int(time.time())}"
        try:
            path.rename(corrupt_path)
            logger.warning(
                "state file %s was unreadable (%s); moved to %s and re-baselining",
                path, exc, corrupt_path,
            )
        except OSError as rename_exc:
            logger.warning(
                "state file %s was unreadable (%s) and could not be moved aside (%s); "
                "re-baselining anyway",
                path, exc, rename_exc,
            )
        return MonitorState()
    return MonitorState(
        last_run_utc=data.get("last_run_utc"),
        last_not_before=data.get("last_not_before", {}),
    )


def save_state(path: Path, state: MonitorState) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"last_run_utc": state.last_run_utc, "last_not_before": state.last_not_before}
    with tempfile.NamedTemporaryFile("w", delete=False, dir=str(path.parent)) as tmp:
        json.dump(payload, tmp, indent=2, sort_keys=True)
        tmp_path = Path(tmp.name)
    tmp_path.replace(path)


def classify(
    env_label: str,
    cert: CertInfo,
    events: list[CaddyEvent],
    state: MonitorState,
    now: datetime,
    expiry_alert_days: int,
) -> list[str]:
    states = []
    identifier = subject_to_identifier(cert.subject)
    prior = state.last_not_before.get(identifier)
    current_iso = cert.not_before.isoformat()

    if prior is not None and current_iso > prior:
        states.append("renewed")

    if any(e.is_failure() for e in events):
        states.append("failed")

    success_count = sum(1 for e in events if e.is_success())
    expected = EXPECTED_SUCCESS_PER_ENV.get(env_label, 1)
    # Partial success only when SOME success but fewer than expected;
    # zero successes is handled by the silent_expiry path.
    if 0 < success_count < expected:
        states.append("partial_success")

    if _is_silent_expiry(cert, events, identifier, now, expiry_alert_days) and "renewed" not in states:
        states.append("silent_expiry")

    return states


def _is_silent_expiry(
    cert: CertInfo, events: list[CaddyEvent], identifier: str,
    now: datetime, expiry_alert_days: int,
) -> bool:
    if any(e.is_success() for e in events):
        return False
    planning = latest_planning_for(identifier, events)
    if planning is not None and planning.selected_time is not None:
        selected_at = datetime.fromtimestamp(planning.selected_time, tz=timezone.utc)
        return now > selected_at + timedelta(days=SILENT_EXPIRY_GRACE_DAYS)
    return (cert.not_after - now) < timedelta(days=expiry_alert_days)


def build_notifications(
    env_label: str, cert: CertInfo, events: list[CaddyEvent],
    states: list[str], notify_on_success: bool,
) -> list[Notification]:
    notes = []
    if "renewed" in states and notify_on_success:
        notes.append(_renewed(env_label, cert))
    if "failed" in states:
        notes.append(_failed(env_label, cert, events))
    if "partial_success" in states:
        notes.append(_partial_success(env_label, cert, events))
    if "silent_expiry" in states:
        notes.append(_silent_expiry(env_label, cert, events))
    return notes


def _renewed(env: str, cert: CertInfo) -> Notification:
    identifier = subject_to_identifier(cert.subject)
    not_after_date = cert.not_after.strftime("%Y-%m-%d")
    not_before_date = cert.not_before.strftime("%Y-%m-%d")
    # Caddy starts attempting renewal 30 days before expiry.
    next_renewal_date = (cert.not_after - timedelta(days=30)).strftime("%Y-%m-%d")
    body = (
        f"Success Notice: Caddy on {env} renewed the TLS cert this past week.\n\n"
        f"  Cert:           {identifier}\n"
        f"  New issuance:   {not_before_date}\n"
        f"  Valid until:    {not_after_date} (90 days from issuance)\n"
        f"  Issued by:      {cert.issuer}\n\n"
        f"The next renewal should happen automatically around {next_renewal_date}\n"
        f"(30 days before expiry). You will get another email like this one\n"
        f"when it lands.\n\n"
        f"No action needed.\n"
    )
    subject_suffix = f"cert renewed, valid until {not_after_date}"
    return Notification(env, "renewed", subject_suffix, body)


def _failed(env: str, cert: CertInfo, events: list[CaddyEvent]) -> Notification:
    identifier = subject_to_identifier(cert.subject)
    failures = [e for e in events if e.is_failure()][-3:]
    retry = next(iter([e for e in events if e.is_retry()][-1:]), None)
    days_left = (cert.not_after - datetime.now(timezone.utc)).days
    not_after_date = cert.not_after.strftime("%Y-%m-%d")
    lines = [
        f"Failure Notice: Caddy on {env} tried to renew the cert this past week and failed.",
        "",
        f"  Cert:             {identifier}",
        f"  Currently valid:  until {not_after_date} ({days_left} days from now)",
        f"  User impact:      none yet — the old cert is still serving",
        "",
        "Why the renewal failed (most recent attempts):",
        "",
    ]
    for e in failures:
        lines.append(f"  [{e.logger_path}] {e.msg}")
        if e.error:
            lines.append(f"    {e.error}")
    lines.append("")
    if retry is not None:
        lines += [
            "Caddy is retrying automatically:",
            f"  - {retry.attempt} attempts so far",
            f"  - {retry.elapsed:.1f} seconds spent trying",
            f"  - It will keep trying for up to 30 days",
            "",
        ]
    lines += [
        "What to do:",
        "",
        "1. SSH to bloom-dev and look at the actual Caddy errors:",
        f"     docker logs --tail=200 bloom_v2_{env}-caddy-1 | grep -i error",
        "",
        "2. Common causes:",
        "     - Cloudflare API token revoked or expired",
        "     - Salk CNAME for _acme-challenge.bloom-dev.salk.edu removed",
        "     - Let's Encrypt rate limit hit (rare)",
        "",
        "3. If you fix the underlying issue, Caddy will succeed on its next",
        "   retry (every ~10 minutes). You do not need to restart anything.",
        "",
        "If the cert reaches < 14 days without renewing, you will get a",
        "separate 'cert expires in N days, no renewal seen' urgent alert.",
        "",
    ]
    subject_suffix = f"renewal failed — cert valid {days_left} more days"
    return Notification(env, "failed", subject_suffix, "\n".join(lines))


def _partial_success(env: str, cert: CertInfo, events: list[CaddyEvent]) -> Notification:
    success_ids = sorted({e.identifier for e in events if e.is_success() and e.identifier})
    expected_ids = EXPECTED_IDENTIFIERS_PER_ENV.get(env, [])
    missing_ids = sorted(set(expected_ids) - set(success_ids))
    expected = EXPECTED_SUCCESS_PER_ENV.get(env, 1)
    lines = [
        f"Partial Failure Notice: Caddy on {env} renewed {len(success_ids)} of {expected} certs this week.",
        "",
        f"  Renewed:     {', '.join(success_ids) if success_ids else '(none)'}",
        f"  Not renewed: {', '.join(missing_ids) if missing_ids else '(unknown — check logs)'}",
        "",
        "Both old certs are still valid for users right now — nothing visible",
        "is broken. But the missing cert needs to figure out why its renewal",
        "failed before it expires.",
        "",
        "What to do:",
        "",
        "1. Check the Caddy logs filtered to the missing identifier:",
    ]
    for ident in missing_ids:
        lines.append(f"     docker logs --tail=200 bloom_v2_{env}-caddy-1 | grep -A 5 \"{ident}\"")
    lines += [
        "",
        "2. Most likely cause: Let's Encrypt failed the DNS-01 challenge for",
        "   the missing cert but succeeded on the other. Could be a Cloudflare",
        "   API hiccup on just one record.",
        "",
        "3. Caddy will retry on its next tick (~10 minutes).",
        "",
    ]
    subject_suffix = f"only {len(success_ids)} of {expected} certs renewed"
    return Notification(env, "partial_success", subject_suffix, "\n".join(lines))


def _silent_expiry(env: str, cert: CertInfo, events: list[CaddyEvent]) -> Notification:
    identifier = subject_to_identifier(cert.subject)
    planning = latest_planning_for(identifier, events)
    days_left = (cert.not_after - datetime.now(timezone.utc)).days
    not_after_date = cert.not_after.strftime("%Y-%m-%d")
    lines = [
        f"Urgent Notice: Caddy on {env} has NOT renewed the cert and it's close to expiry.",
        "",
        f"  Cert:        {identifier}",
        f"  Expires:     {not_after_date} ({days_left} days from now)",
    ]
    if planning is not None and planning.selected_time is not None:
        planned = datetime.fromtimestamp(planning.selected_time, tz=timezone.utc)
        days_overdue = (datetime.now(timezone.utc) - planned).days
        lines.append(
            f"  Should have renewed: {planned.strftime('%Y-%m-%d')} "
            f"({days_overdue} days ago — should have happened)"
        )
    lines += [
        "",
        "What this means:",
        "",
        "The monitor compared today's cert against the one it saw last week —",
        "same cert. Caddy was supposed to have renewed by now but didn't. We",
        f"have {days_left} days before browsers start showing TLS errors to users.",
        "",
        "Why this might be happening:",
        "",
        "  1. Earlier 'renewal failed' alerts that no one acted on (check inbox)",
        "  2. The Caddy container has been stopped longer than usual",
        "  3. Something the monitor hasn't seen yet",
        "",
        "What to do RIGHT NOW:",
        "",
        "1. SSH to bloom-dev and check Caddy:",
        "     systemctl status docker",
        "     docker ps | grep caddy",
        f"     docker logs --tail=300 bloom_v2_{env}-caddy-1 | grep -i error",
        "",
        "2. Find the actual obstacle — Cloudflare token, DNS record, container",
        "   restart, anything that's preventing renewal.",
        "",
        "3. If the cert is going to expire in days, you can force a renewal:",
        f"     docker exec bloom_v2_{env}-caddy-1 caddy reload --config /etc/caddy/Caddyfile",
        "",
        "If this email is a false alarm (e.g. you renewed manually and the",
        "monitor's state file is stale), you can ignore it. The next weekly",
        "run will re-baseline.",
        "",
    ]
    subject_suffix = f"cert expires in {days_left} days, no renewal seen"
    return Notification(env, "silent_expiry", subject_suffix, "\n".join(lines))


def build_caddy_unreachable_notification(env: str, prior_state: MonitorState, docker_error: str) -> Notification:
    """Build a notification used when the monitor cannot reach Caddy at all.

    Carries the last-known cert state from the state file so the admin can see
    when we last successfully observed the cert, and includes a short diagnostic
    runbook.
    """
    lines = [
        f"Warning Notice: Bloom-cert-monitor could not reach the {env} Caddy",
        "container to check the cert this week.",
        "",
        "What docker said when we tried:",
        "",
        f"  {docker_error}",
        "",
    ]
    if prior_state.last_run_utc:
        lines.append(f"Last successful observation: {prior_state.last_run_utc}")
    if prior_state.last_not_before:
        lines.append("At that time the certs we knew about were:")
        for identifier, not_before in sorted(prior_state.last_not_before.items()):
            lines.append(f"  {identifier} — notBefore={not_before}")
    else:
        lines.append("No prior observation in the state file (first-ever run, or state cleared).")
    lines += [
        "",
        "We have no fresh data on whether the cert was renewed since that prior",
        "observation. The next weekly run will resume normal observation if",
        "Caddy comes back.",
        "",
        "What to do:",
        "",
        "  - If Caddy was down only for a deploy or short restart, this email",
        "    can be safely ignored. Next Sunday's run will catch up.",
        "  - If Caddy is unexpectedly down, investigate:",
        "",
        f"      docker logs --tail=100 bloom_v2_{env}-caddy-1",
        "      systemctl status docker",
        "",
    ]
    return Notification(env, "caddy_unreachable", "Caddy container is down", "\n".join(lines))


def _format_subject(notification: Notification) -> str:
    """Build the email subject line.

    Preflight skips the env segment ("preflight preflight" reads as a
    duplication bug, not a useful label). Everything else gets the
    env uppercased so prod/staging are visually distinct in inbox lists.
    """
    if notification.env == "preflight":
        return f"[bloom-cert-monitor] {notification.subject_suffix}"
    return f"[bloom-cert-monitor] {notification.env.upper()}: {notification.subject_suffix}"


def send_email(notification: Notification, sender: str, recipients: list[str], smtp_host: str) -> None:
    msg = EmailMessage()
    msg["From"] = sender
    msg["To"] = ", ".join(recipients)
    msg["Subject"] = _format_subject(notification)
    msg.set_content(notification.body)
    with smtplib.SMTP(smtp_host, 25, timeout=30) as smtp:
        smtp.send_message(msg)
