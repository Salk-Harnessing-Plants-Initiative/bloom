#!/usr/bin/env python3
"""Weekly cert renewal monitor for bloom-dev's Caddy stacks.

See `.env.{staging,prod}.defaults` for the CERT_MONITOR_* config surface.
Exit 0=clean, 1=subprocess error, 2=SMTP error.
"""

from __future__ import annotations

import argparse
import logging
import os
import smtplib
import sys
from datetime import datetime, timezone
from pathlib import Path

from monitor_lib import (
    CaddyEnv, MonitorState, Notification,
    build_caddy_unreachable_notification, build_notifications, check_container_running,
    classify, list_cert_subjects, load_state, parse_events,
    read_caddy_logs, read_cert, save_state, send_email, subject_to_identifier,
)

CADDY_ENVS = [
    CaddyEnv(
        label="staging",
        container="bloom_v2_staging-caddy-1",
        cert_dir="/data/caddy/certificates/acme-v02.api.letsencrypt.org-directory",
    ),
    CaddyEnv(
        label="prod",
        container="bloom_v2_prod-caddy-1",
        cert_dir="/data/caddy/certificates/acme-v02.api.letsencrypt.org-directory",
    ),
]

logger = logging.getLogger("bloom_cert_monitor")


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env", choices=["staging", "prod"],
                        help="Restrict the run to a single environment. Required when invoked "
                             "from a per-env systemd service. Omit for one-off CLI use that "
                             "checks both envs.")
    parser.add_argument("--force-notification", choices=["preflight"],
                        help="Send a synthetic notification (used by installer --test-send)")
    args = parser.parse_args(argv)

    envs_to_process = CADDY_ENVS if args.env is None else [e for e in CADDY_ENVS if e.label == args.env]

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    recipients = [r.strip() for r in _env("CERT_MONITOR_RECIPIENTS").split(",") if r.strip()]
    smtp_host = _env("CERT_MONITOR_SMTP_HOST", "neoemex1.salk.edu")
    sender = _env("CERT_MONITOR_FROM", "bloom-cert-monitor@bloom-dev.salk.edu")
    state_dir = Path(_env("CERT_MONITOR_STATE_DIR", "/var/lib/bloom-cert-monitor"))
    expiry_alert_days = int(_env("CERT_MONITOR_EXPIRY_ALERT_DAYS", "14"))
    notify_on_success = _env("CERT_MONITOR_NOTIFY_ON_SUCCESS", "true").lower() == "true"

    if not recipients:
        logger.error("CERT_MONITOR_RECIPIENTS is empty; refusing to run")
        return 1

    if args.force_notification == "preflight":
        return _send_preflight(sender, recipients, smtp_host)

    now = datetime.now(timezone.utc)
    smtp_failure = False
    for caddy_env in envs_to_process:
        try:
            _process_env(caddy_env, now, state_dir, expiry_alert_days, notify_on_success,
                         sender, recipients, smtp_host)
        except RuntimeError as exc:
            logger.error("env %s failed: %s", caddy_env.label, exc)
            return 1
        except smtplib.SMTPException as exc:
            logger.error("env %s SMTP error: %s", caddy_env.label, exc)
            smtp_failure = True

    return 2 if smtp_failure else 0


def _send_preflight(sender: str, recipients: list[str], smtp_host: str) -> int:
    note = Notification(
        env="preflight", kind="preflight",
        subject_suffix="preflight test — SMTP delivery works",
        body=(
            "This is a one-off test email from bloom-cert-monitor (you triggered\n"
            "it via the installer's --test-send flag).\n\n"
            "If you got this email, the path from bloom-dev → Salk's mail relay\n"
            "→ your inbox is working. The weekly scheduled runs will use this\n"
            "same delivery path.\n\n"
            "No action needed.\n"
        ),
    )
    # Catch SMTP and OSError specifically — don't blanket-catch Exception, since
    # that swallows KeyboardInterrupt-style aborts and programming bugs that
    # should surface loudly.
    try:
        send_email(note, sender, recipients, smtp_host)
    except (smtplib.SMTPException, OSError) as exc:
        logger.error("preflight SMTP send failed: %s", exc)
        return 2
    logger.info("preflight email sent to %s", recipients)
    return 0


def _process_env(
    caddy_env: CaddyEnv, now: datetime, state_dir: Path, expiry_alert_days: int,
    notify_on_success: bool, sender: str, recipients: list[str], smtp_host: str,
) -> None:
    state_path = state_dir / f"{caddy_env.label}.json"
    state = load_state(state_path)
    new_state = MonitorState(last_run_utc=now.isoformat(), last_not_before=dict(state.last_not_before))

    # If Caddy is unreachable (stopped, missing, docker daemon down, etc.) we
    # can't observe the cert this run. Emit a "caddy_unreachable" notification
    # instead of false-alarming as a renewal failure — keeps the monitor's
    # silence unambiguous: no email = healthy, email = something to look at.
    is_running, docker_error = check_container_running(caddy_env)
    if not is_running:
        logger.warning("env %s: Caddy container unreachable: %s", caddy_env.label, docker_error)
        note = build_caddy_unreachable_notification(caddy_env.label, state, docker_error or "")
        try:
            send_email(note, sender, recipients, smtp_host)
            logger.info("env %s: sent caddy_unreachable notification", caddy_env.label)
        finally:
            save_state(state_path, new_state)
        return

    subjects = list_cert_subjects(caddy_env)
    if not subjects:
        logger.info("env %s: no cert dirs found", caddy_env.label)
        save_state(state_path, new_state)
        return
    events = parse_events(read_caddy_logs(caddy_env))

    try:
        for subject_dir in subjects:
            cert = read_cert(caddy_env, subject_dir)
            identifier = subject_to_identifier(cert.subject)
            states = classify(caddy_env.label, cert, events, state, now, expiry_alert_days)
            # Record the observation in state BEFORE attempting to send. If
            # SMTP raises and propagates out, the finally block still saves
            # what we observed — so next week we don't re-emit on the same
            # renewal/failure transition.
            new_state.last_not_before[identifier] = cert.not_before.isoformat()
            for note in build_notifications(caddy_env.label, cert, events, states, notify_on_success):
                send_email(note, sender, recipients, smtp_host)
                logger.info("env %s: sent %s notification for %s", caddy_env.label, note.kind, identifier)
    finally:
        save_state(state_path, new_state)


if __name__ == "__main__":
    sys.exit(main())
