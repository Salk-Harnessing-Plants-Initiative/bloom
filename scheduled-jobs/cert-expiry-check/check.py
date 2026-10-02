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


@dataclass(frozen=True)
class Cert:
    """The certificate a site served: when it expires, its serial, and the names it covers."""

    expires: datetime
    serial: str
    names: tuple[str, ...]


@dataclass
class Result:
    host: str
    port: int
    expires: datetime | None = None
    days_left: int | None = None
    problem: str | None = None
    ok: bool = False
    serial: str | None = None
    names: tuple[str, ...] = ()

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


def env_name(env_file: Path) -> str:
    """'prod' for .env.prod.defaults."""
    return env_file.name.removeprefix(".env.").removesuffix(".defaults")


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


def tls_context() -> ssl.SSLContext:
    """Verifies the chain and the hostname, and refuses anything older than TLS 1.2."""
    context = ssl.create_default_context()
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    return context


def fetch_expiry(host: str, port: int) -> Cert:
    """The verified certificate the site serves, or CertProblem saying why it can't be read."""
    context = tls_context()
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
    return Cert(
        expires=datetime.fromtimestamp(
            ssl.cert_time_to_seconds(cert["notAfter"]), tz=UTC
        ),
        serial=cert.get("serialNumber", ""),
        names=tuple(v for k, v in cert.get("subjectAltName", ()) if k == "DNS"),
    )


def check(
    hosts: list[tuple[str, int]],
    now: datetime,
    warn_days: int,
    fetch: Callable[[str, int], Cert] | None = None,
) -> list[Result]:
    fetch = fetch or fetch_expiry  # looked up at call time, so tests can replace it
    results = []
    for host, port in hosts:
        try:
            cert = fetch(host, port)
        except CertProblem as exc:
            results.append(Result(host, port, problem=str(exc)))
            continue
        days = (cert.expires - now).days
        results.append(
            Result(
                host,
                port,
                cert.expires,
                days,
                ok=days >= warn_days,
                serial=cert.serial,
                names=cert.names,
            )
        )
    return results


def _certificates(results: list[Result]) -> list[list[Result]]:
    """Expiring results grouped by the certificate that served them, most urgent first."""
    groups: dict[str, list[Result]] = {}
    for r in results:
        if not r.ok and not r.problem:
            groups.setdefault(r.serial or r.site, []).append(r)
    return sorted(groups.values(), key=lambda g: g[0].days_left)


def _plural(n: int, word: str) -> str:
    return f"{n} {word}" if n == 1 else f"{n} {word}s"


def build_alert(results: list[Result], warn_days: int, env: str) -> tuple[str, str]:
    certificates = _certificates(results)
    unreadable = [r for r in results if r.problem]
    if certificates:
        first = certificates[0][0]
        name = ", ".join(first.names) or first.host
        subject = f"[bloom-cert-check] {env}: certificate for {name} expires in {first.days_left} days"
        others = len(certificates) - 1 + len(unreadable)
        if others:
            subject += f", {others} more {'problem' if others == 1 else 'problems'}"
    else:
        subject = f"[bloom-cert-check] {env}: {_plural(len(unreadable), 'site')} could not be checked"

    lines = [
        f"Urgent Notice: a {env} TLS certificate for Bloom needs attention.",
        "",
        "Caddy renews certificates about 30 days before they expire, so none should",
        f"ever have fewer than {warn_days} days left.",
        "",
    ]
    if certificates:
        lines.append(f"Expiring soon (fewer than {warn_days} days left):")
        for group in certificates:
            first = group[0]
            name = ", ".join(first.names) or first.host
            lines.append(
                f"  {name}: expires in {first.days_left} days ({first.expires:%Y-%m-%d})"
            )
            lines.append(f"    used by {', '.join(r.host for r in group)}")
        lines.append("")
    if unreadable:
        lines.append("Could not be checked:")
        lines += [f"  {r.site}: {r.problem}" for r in unreadable]
        lines.append("")
    lines += [
        "When a certificate expires, browsers refuse the site and the scanners'",
        "uploads fail.",
        "",
        "What to do:",
        "",
        "1. SSH to bloom-dev and look at Caddy's renewal errors:",
        f"     docker logs --since 168h bloom_v2_{env}-caddy-1 2>&1 | grep -i -E 'error|obtain'",
        "",
        "2. Common causes:",
        "     - Cloudflare API token revoked or expired",
        f"     - Salk CNAME {ACME_CHALLENGE_NAME} -> {ACME_CNAME_TARGET} removed",
        "     - Caddy stopped, so nothing is renewing",
        "",
        "3. Once the cause is fixed, Caddy retries on its own, though after repeated",
        "   failures its retries can be hours apart. A new Cloudflare token only takes",
        "   effect after a redeploy. Re-run this check from the Actions tab to confirm.",
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
        smtp.ehlo()
        if smtp.has_extn(
            "starttls"
        ):  # encrypt when the relay offers it, as GoTrue does
            smtp.starttls(context=ssl.create_default_context())
            smtp.ehlo()
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
        "| Site | Certificate | Expires | Days left | Status |",
        "| --- | --- | --- | --- | --- |",
    ]
    for r in results:
        if r.problem:
            rows.append(f"| {r.site} | — | — | — | could not be checked: {r.problem} |")
        else:
            status = "ok" if r.ok else "renew now"
            rows.append(
                f"| {r.site} | {', '.join(r.names) or '—'} | {r.expires:%Y-%m-%d} | {r.days_left} | {status} |"
            )
    try:
        with open(path, "a") as fh:
            fh.write("\n".join(rows) + "\n")
    except OSError as exc:  # the summary is a convenience; never let it fail the run
        print(f"could not write the run summary: {exc}", file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--env-file",
        action="append",
        type=Path,
        default=[],
        help="The environment's env defaults file, naming the sites to check. "
        "The first one also supplies the mail relay, the sender and the environment name.",
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
    env = env_name(args.env_file[0])
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
    if all(r.ok for r in results):
        write_summary(results, warn_days)
        return 0

    # The alert goes first: nothing after this point may stop it.
    subject, body = build_alert(results, warn_days, env)
    try:
        send_email(subject, body, mail, recipients)
    except (smtplib.SMTPException, OSError) as exc:
        print(f"alert email failed: {exc}", file=sys.stderr)
        write_summary(results, warn_days)
        return 2
    print(f"alert emailed to {', '.join(recipients)}")
    write_summary(results, warn_days)
    return 1


if __name__ == "__main__":
    sys.exit(main())
