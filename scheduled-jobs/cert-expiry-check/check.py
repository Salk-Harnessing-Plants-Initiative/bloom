#!/usr/bin/env python3
"""Weekly check of the TLS certificates Bloom's sites serve.

Connects to each site named in the given env defaults files, reads the expiry
date of the certificate it serves, and emails the team when one is close to
expiring or can't be checked. Mail goes out the way the site's own email does:
the SMTP_* relay and sender from the first env file. Caddy renews about 30 days
before expiry, so a healthy certificate never gets under the warning line.

Exit 0 = every certificate is fine, 1 = a problem was found (and emailed),
2 = a problem was found but the email could not be sent.
"""

from __future__ import annotations

import argparse
import os
import smtplib
import socket
import ssl
import sys
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from email.message import EmailMessage
from email.utils import formataddr
from pathlib import Path

DEFAULT_WARN_DAYS = 21
CONNECT_TIMEOUT_SECONDS = 10
SMTP_TIMEOUT_SECONDS = 30
HOST_KEYS = ("DOMAIN_MAIN", "DOMAIN_STUDIO", "DOMAIN_MINIO")
ACME_CHALLENGE_NAME = "_acme-challenge.bloom.salk.edu"
ACME_CNAME_TARGET = "_acme-challenge.bloom-acme.talmolab.org"


class CertProblem(Exception):
    """A site's certificate could not be read or verified."""


@dataclass
class Result:
    host: str
    port: int
    expires: datetime | None = None
    days_left: int | None = None
    problem: str | None = None
    ok: bool = False

    @property
    def site(self) -> str:
        return f"{self.host}:{self.port}"


@dataclass(frozen=True)
class MailSettings:
    host: str
    port: int
    address: str
    name: str


def utc_now() -> datetime:
    return datetime.now(UTC)


def _read_env(path: Path) -> dict[str, str]:
    values = {}
    for line in path.read_text().splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            key, _, value = line.partition("=")
            values[key.strip()] = value.strip()
    return values


def served_hosts(env_file: Path) -> list[tuple[str, int]]:
    """The (host, port) pairs Caddy serves for one environment."""
    values = _read_env(env_file)
    if not values.get("DOMAIN_MAIN"):
        raise ValueError(f"{env_file} has no DOMAIN_MAIN")
    port = int(values.get("CADDY_HTTPS_LISTEN_PORT", "443"))
    return [(values[key], port) for key in HOST_KEYS if values.get(key)]


def mail_settings(env_file: Path) -> MailSettings:
    """Bloom's relay and sender, the SMTP_* settings the site's auth email uses."""
    values = _read_env(env_file)
    missing = [k for k in ("SMTP_HOST", "SMTP_ADMIN_EMAIL") if not values.get(k)]
    if missing:
        raise ValueError(f"{env_file} has no {', '.join(missing)}")
    return MailSettings(
        host=values["SMTP_HOST"],
        port=int(values.get("SMTP_PORT", "25")),
        address=values["SMTP_ADMIN_EMAIL"],
        name=values.get("SMTP_SENDER_NAME", "Bloom"),
    )


def fetch_expiry(host: str, port: int) -> datetime:
    """The verified certificate's notAfter, or CertProblem saying why it can't be read."""
    context = ssl.create_default_context()
    try:
        with (
            socket.create_connection(
                (host, port), timeout=CONNECT_TIMEOUT_SECONDS
            ) as sock,
            context.wrap_socket(sock, server_hostname=host) as tls,
        ):
            cert = tls.getpeercert()
    except ssl.SSLCertVerificationError as exc:
        raise CertProblem(exc.verify_message or str(exc)) from exc
    except (OSError, ssl.SSLError) as exc:
        raise CertProblem(str(exc) or type(exc).__name__) from exc
    return datetime.fromtimestamp(ssl.cert_time_to_seconds(cert["notAfter"]), tz=UTC)


def check(
    hosts: list[tuple[str, int]],
    now: datetime,
    warn_days: int,
    fetch: Callable[[str, int], datetime] | None = None,
) -> list[Result]:
    fetch = fetch or fetch_expiry  # looked up at call time, so tests can replace it
    results = []
    for host, port in hosts:
        try:
            expires = fetch(host, port)
        except CertProblem as exc:
            results.append(Result(host, port, problem=str(exc)))
            continue
        days = (expires - now).days
        results.append(Result(host, port, expires, days, ok=days >= warn_days))
    return results


def build_alert(results: list[Result], warn_days: int) -> tuple[str, str]:
    problems = [r for r in results if not r.ok]
    unreadable = [r for r in problems if r.problem]
    expiring = sorted((r for r in problems if not r.problem), key=lambda r: r.days_left)
    if expiring:
        first = expiring[0]
        subject = (
            f"[bloom-cert-check] {first.host} cert expires in {first.days_left} days"
        )
    else:
        subject = f"[bloom-cert-check] {unreadable[0].host} could not be checked"
    lines = [
        "Urgent Notice: a Bloom TLS certificate needs attention.",
        "",
        "Caddy renews certificates about 30 days before they expire, so none should",
        f"ever have fewer than {warn_days} days left. These do:",
        "",
    ]
    for r in expiring:
        lines.append(
            f"  {r.site}  expires in {r.days_left} days ({r.expires:%Y-%m-%d})"
        )
    for r in unreadable:
        lines.append(f"  {r.site}  could not be checked: {r.problem}")
    lines += [
        "",
        "When a certificate expires, browsers refuse the site and the scanners'",
        "uploads fail.",
        "",
        "What to do:",
        "",
        "1. SSH to bloom-dev and look at Caddy's renewal errors:",
        "     docker logs --since 168h bloom_v2_prod-caddy-1 2>&1 | grep -i -E 'error|obtain'",
        "     (bloom_v2_staging-caddy-1 for the staging sites)",
        "",
        "2. Common causes:",
        "     - Cloudflare API token revoked or expired",
        f"     - Salk CNAME {ACME_CHALLENGE_NAME} -> {ACME_CNAME_TARGET} removed",
        "     - Caddy stopped, so nothing is renewing",
        "",
        "3. Once the cause is fixed Caddy renews on its own within minutes.",
        "   Re-run this check from the Actions tab to confirm.",
        "",
    ]
    return subject, "\n".join(lines)


def send_email(
    subject: str, body: str, mail: MailSettings, recipients: list[str]
) -> None:
    msg = EmailMessage()
    msg["From"] = formataddr((mail.name, mail.address))
    msg["To"] = ", ".join(recipients)
    msg["Subject"] = subject
    msg.set_content(body)
    with smtplib.SMTP(mail.host, mail.port, timeout=SMTP_TIMEOUT_SECONDS) as smtp:
        smtp.send_message(msg)


def write_summary(results: list[Result], warn_days: int) -> None:
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not path:
        return
    rows = [
        "## TLS certificates",
        "",
        f"Warning line: {warn_days} days.",
        "",
        "| Site | Expires | Days left | Status |",
        "| --- | --- | --- | --- |",
    ]
    for r in results:
        if r.problem:
            rows.append(f"| {r.site} | — | — | could not be checked: {r.problem} |")
        else:
            rows.append(
                f"| {r.site} | {r.expires:%Y-%m-%d} | {r.days_left} | {'ok' if r.ok else 'renew now'} |"
            )
    with open(path, "a") as fh:
        fh.write("\n".join(rows) + "\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--env-file",
        action="append",
        type=Path,
        default=[],
        help="An env defaults file naming the sites to check (repeatable). "
        "The first one also supplies the mail relay and sender.",
    )
    parser.add_argument(
        "--test-email",
        action="store_true",
        help="Send one test email to confirm the relay works, and check nothing.",
    )
    args = parser.parse_args(argv)

    recipients = [
        r.strip()
        for r in os.environ.get("CERT_CHECK_RECIPIENTS", "").split(",")
        if r.strip()
    ]
    if not recipients:
        print("CERT_CHECK_RECIPIENTS is empty; refusing to run", file=sys.stderr)
        return 1
    if not args.env_file:
        print("give at least one --env-file", file=sys.stderr)
        return 1
    mail = mail_settings(args.env_file[0])
    warn_days = int(os.environ.get("CERT_CHECK_WARN_DAYS", DEFAULT_WARN_DAYS))

    if args.test_email:
        try:
            send_email(
                "[bloom-cert-check] test email: the relay works",
                "This is a test from the weekly Bloom certificate check.\n"
                "If you got it, alerts will reach you. No action needed.\n",
                mail,
                recipients,
            )
        except (smtplib.SMTPException, OSError) as exc:
            print(f"test email failed: {exc}", file=sys.stderr)
            return 2
        print(f"test email sent to {', '.join(recipients)}")
        return 0

    hosts = [h for env_file in args.env_file for h in served_hosts(env_file)]
    results = check(hosts, utc_now(), warn_days)
    for r in results:
        status = (
            f"could not be checked: {r.problem}"
            if r.problem
            else f"{r.days_left} days left"
        )
        print(f"{r.site}: {status}")
    write_summary(results, warn_days)
    if all(r.ok for r in results):
        return 0

    subject, body = build_alert(results, warn_days)
    try:
        send_email(subject, body, mail, recipients)
    except (smtplib.SMTPException, OSError) as exc:
        print(f"alert email failed: {exc}", file=sys.stderr)
        return 2
    print(f"alert emailed to {', '.join(recipients)}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
