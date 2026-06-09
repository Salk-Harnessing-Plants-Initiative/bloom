"""Helpers for check_cert_renewal.py — parsing, classification, email.

Pure functions wherever possible so the test file can exercise them
against the fixture .jsonl files in check_cert_renewal_test_fixtures/.
"""

from __future__ import annotations

import json
import smtplib
import subprocess
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from pathlib import Path
from typing import Iterable

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
    kind: str  # renewed | failed | silent_expiry | partial_success | preflight
    subject_suffix: str
    body: str


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
    if not path.exists():
        return MonitorState()
    raw = json.loads(path.read_text())
    return MonitorState(
        last_run_utc=raw.get("last_run_utc"),
        last_not_before=raw.get("last_not_before", {}),
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
    body = (
        f"Caddy on {env} renewed the cert for {cert.subject}.\n\n"
        f"  notBefore: {cert.not_before.isoformat()}\n"
        f"  notAfter:  {cert.not_after.isoformat()}\n"
        f"  issuer:    {cert.issuer}\n"
    )
    return Notification(env, "renewed", "renewed", body)


def _failed(env: str, cert: CertInfo, events: list[CaddyEvent]) -> Notification:
    failures = [e for e in events if e.is_failure()][-3:]
    retry = next(iter([e for e in events if e.is_retry()][-1:]), None)
    days_left = (cert.not_after - datetime.now(timezone.utc)).days
    lines = [
        f"Caddy on {env} reported a renewal failure for {cert.subject}.",
        f"Cert currently serves until: {cert.not_after.isoformat()} ({days_left} days runway)",
        "",
        "Most recent failure events:",
    ]
    lines += [f"  [{e.logger_path}] {e.msg}: {e.error or '(no error field)'}" for e in failures]
    if retry is not None:
        lines.append(f"\nCaddy retry-in-progress: attempt={retry.attempt} elapsed={retry.elapsed}s")
    return Notification(env, "failed", "failed", "\n".join(lines) + "\n")


def _partial_success(env: str, cert: CertInfo, events: list[CaddyEvent]) -> Notification:
    success_ids = sorted({e.identifier for e in events if e.is_success() and e.identifier})
    expected = EXPECTED_SUCCESS_PER_ENV.get(env, 1)
    body = (
        f"Caddy on {env} obtained only {len(success_ids)} of {expected} expected certs.\n\n"
        f"Obtained for: {', '.join(success_ids)}\n"
        f"Currently-served cert subject: {cert.subject}\n"
    )
    return Notification(env, "partial_success", "partial_success", body)


def _silent_expiry(env: str, cert: CertInfo, events: list[CaddyEvent]) -> Notification:
    planning = latest_planning_for(subject_to_identifier(cert.subject), events)
    days_left = (cert.not_after - datetime.now(timezone.utc)).days
    body = (
        f"Caddy on {env} has not obtained a fresh cert for {cert.subject}.\n\n"
        f"Cert expires: {cert.not_after.isoformat()} ({days_left} days remaining)\n"
    )
    if planning is not None and planning.selected_time is not None:
        planned = datetime.fromtimestamp(planning.selected_time, tz=timezone.utc)
        body += f"Caddy planned to renew at: {planned.isoformat()} (already passed)\n"
    return Notification(env, "silent_expiry", "silent_expiry", body)


def send_email(notification: Notification, sender: str, recipients: list[str], smtp_host: str) -> None:
    msg = EmailMessage()
    msg["From"] = sender
    msg["To"] = ", ".join(recipients)
    msg["Subject"] = f"[bloom-cert-monitor] {notification.env} {notification.subject_suffix}"
    msg.set_content(notification.body)
    with smtplib.SMTP(smtp_host, 25, timeout=30) as smtp:
        smtp.send_message(msg)
