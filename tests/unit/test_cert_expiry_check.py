"""The weekly TLS certificate expiry check and its workflow."""

from __future__ import annotations

import smtplib
import ssl
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import yaml

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scheduled-jobs" / "cert-expiry-check"))

import check  # noqa: E402

NOW = datetime(2026, 10, 2, 16, 0, tzinfo=timezone.utc)
WORKFLOW = REPO / ".github" / "workflows" / "cert-expiry-check.yml"
PROD = REPO / ".env.prod.defaults"
APEX = ("bloom.salk.edu",)
WILDCARD = ("*.bloom.salk.edu",)


def _cert(days, serial="APEX", names=APEX):
    return check.Cert(NOW + timedelta(days=days, hours=1), serial, names)


def _expiring_in(days):
    return lambda host, port: _cert(days)


def _prod_certs(apex_days, wildcard_days):
    """Prod's two certificates: the apex has its own, studio. and minio. share the wildcard."""

    def fetch(host, port):
        if host == "bloom.salk.edu":
            return _cert(apex_days, "APEX", APEX)
        return _cert(wildcard_days, "WILD", WILDCARD)

    return fetch


PROD_HOSTS = [("bloom.salk.edu", 443), ("studio.bloom.salk.edu", 443), ("minio.bloom.salk.edu", 443)]


# --- which sites are checked ---------------------------------------------------------


def test_prod_checks_the_three_sites_caddy_serves_on_443():
    assert check.served_hosts(PROD) == PROD_HOSTS


def test_an_env_file_without_a_main_domain_is_refused(tmp_path):
    f = tmp_path / ".env.x.defaults"
    f.write_text("CADDY_HTTPS_LISTEN_PORT=443\n")
    with pytest.raises(ValueError, match="DOMAIN_MAIN"):
        check.served_hosts(f)


def test_the_environment_name_comes_from_the_env_file_name():
    assert check.env_name(PROD) == "prod"


# --- the decision ---------------------------------------------------------------------


def test_a_cert_with_plenty_of_time_is_fine():
    (r,) = check.check([("bloom.salk.edu", 443)], NOW, warn_days=21, fetch=_expiring_in(60))
    assert r.ok and r.days_left == 60


@pytest.mark.parametrize("days, ok", [(21, True), (20, False)])
def test_the_warning_line_is_inclusive(days, ok):
    (r,) = check.check([("bloom.salk.edu", 443)], NOW, warn_days=21, fetch=_expiring_in(days))
    assert r.ok is ok


def test_a_site_that_cannot_be_checked_is_a_problem_not_a_crash():
    def broken(host, port):
        raise check.CertProblem("certificate has expired")

    (r,) = check.check([("bloom.salk.edu", 443)], NOW, warn_days=21, fetch=broken)
    assert not r.ok and r.problem == "certificate has expired"


def test_each_result_records_which_certificate_served_it():
    results = check.check(PROD_HOSTS, NOW, 21, fetch=_prod_certs(60, 60))
    assert [r.serial for r in results] == ["APEX", "WILD", "WILD"]


# --- the email --------------------------------------------------------------------------


def test_the_email_lists_one_line_per_certificate_with_the_sites_it_serves():
    results = check.check(PROD_HOSTS, NOW, 21, fetch=_prod_certs(60, 5))
    subject, body = check.build_alert(results, 21, "prod")
    lines = body.splitlines()

    assert subject == "[bloom-cert-check] prod: certificate for *.bloom.salk.edu expires in 5 days"
    assert "  *.bloom.salk.edu: expires in 5 days (2026-10-07)" in lines
    assert "    used by studio.bloom.salk.edu, minio.bloom.salk.edu" in lines
    assert sum("expires in" in line for line in lines) == 1, "one line for the shared wildcard"
    assert not any(line.startswith("  bloom.salk.edu:") for line in lines), "the healthy apex is left out"


def test_expiring_and_unreachable_sites_are_under_separate_headings():
    def fetch(host, port):
        if host == "minio.bloom.salk.edu":
            raise check.CertProblem("connection timed out")
        return _prod_certs(10, 60)(host, port)

    subject, body = check.build_alert(check.check(PROD_HOSTS, NOW, 21, fetch=fetch), 21, "prod")
    lines = body.splitlines()

    assert subject == "[bloom-cert-check] prod: certificate for bloom.salk.edu expires in 10 days, 1 more problem"
    assert lines.index("Expiring soon (fewer than 21 days left):") < lines.index("Could not be checked:")
    assert "  minio.bloom.salk.edu:443: connection timed out" in lines


def test_only_unreachable_sites_say_so_in_the_subject():
    def broken(host, port):
        raise check.CertProblem("connection refused")

    subject, body = check.build_alert(check.check(PROD_HOSTS[:1], NOW, 21, fetch=broken), 21, "prod")
    assert subject == "[bloom-cert-check] prod: 1 site could not be checked"
    assert "Expiring soon (fewer than 21 days left):" not in body.splitlines()


def test_the_email_names_the_prod_environment_and_its_container():
    results = check.check(PROD_HOSTS, NOW, 21, fetch=_prod_certs(5, 60))
    _, body = check.build_alert(results, 21, "prod")
    lines = body.splitlines()

    assert lines[0] == "Urgent Notice: a prod TLS certificate for Bloom needs attention."
    assert "     docker logs --since 168h bloom_v2_prod-caddy-1 2>&1 | grep -i -E 'error|obtain'" in lines


# --- the command ------------------------------------------------------------------------


@pytest.fixture
def config(monkeypatch, tmp_path):
    monkeypatch.setenv("CERT_CHECK_RECIPIENTS", "a@salk.edu, b@salk.edu")
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(tmp_path / "summary.md"))
    sent = []
    monkeypatch.setattr(check, "send_email", lambda *a: sent.append(a))
    return sent, tmp_path / "summary.md"


def _run(*args, apex=60, wildcard=60):
    with patch.object(check, "fetch_expiry", _prod_certs(apex, wildcard)), \
         patch.object(check, "utc_now", return_value=NOW):
        return check.main(["--env-file", str(PROD), *args])


def test_all_fine_sends_no_email_and_passes(config):
    sent, summary = config
    assert _run() == 0
    assert sent == []
    assert "| bloom.salk.edu:443 | bloom.salk.edu | 2026-12-01 | 60 | ok |" in summary.read_text().splitlines()


def test_a_problem_emails_the_team_and_fails_the_run(config):
    sent, _ = config
    assert _run(apex=10) == 1
    (args,) = sent
    subject, body, mail, recipients = args
    assert recipients == ["a@salk.edu", "b@salk.edu"]
    assert mail == check.mail_settings(PROD)
    assert subject == "[bloom-cert-check] prod: certificate for bloom.salk.edu expires in 10 days"


def test_a_relay_that_refuses_still_fails_the_run(config, monkeypatch):
    def refuse(*a):
        raise ConnectionRefusedError("relay down")

    monkeypatch.setattr(check, "send_email", refuse)
    assert _run(apex=10) == 2


def test_the_test_email_option_sends_one_email_and_checks_nothing(config):
    sent, _ = config
    with patch.object(check, "fetch_expiry", side_effect=AssertionError("no check in test mode")):
        assert check.main(["--env-file", str(PROD), "--test-email"]) == 0
    assert len(sent) == 1 and "test" in sent[0][0].lower()


def test_no_recipients_refuses_to_run(monkeypatch):
    monkeypatch.delenv("CERT_CHECK_RECIPIENTS", raising=False)
    assert check.main(["--env-file", str(PROD)]) == 1


# --- TLS to the sites -------------------------------------------------------------------


def test_connections_to_the_sites_refuse_anything_older_than_tls_1_2():
    context = check.tls_context()
    assert context.minimum_version == ssl.TLSVersion.TLSv1_2
    assert context.verify_mode == ssl.CERT_REQUIRED and context.check_hostname


# --- mail -------------------------------------------------------------------------------


MAIL = check.MailSettings(host="relay.test", port=2525, address="noreply@test", name="Bloom")


def test_mail_goes_out_as_blooms_own_sender_through_its_relay():
    mail = check.mail_settings(PROD)
    assert (mail.host, mail.port) == ("neoemex1.salk.edu", 25)
    assert mail.address == "noreply@bloom.salk.edu" and mail.name == "Bloom"


def test_an_env_file_without_smtp_settings_is_refused(tmp_path):
    f = tmp_path / ".env.x.defaults"
    f.write_text("DOMAIN_MAIN=x.test\n")
    with pytest.raises(ValueError, match="SMTP_HOST"):
        check.mail_settings(f)


def _smtp(offers_starttls):
    smtp_class = MagicMock()
    smtp = smtp_class.return_value.__enter__.return_value
    smtp.has_extn.side_effect = lambda name: offers_starttls and name.lower() == "starttls"
    return smtp_class, smtp


def test_send_email_uses_the_configured_relay_and_sender_name():
    smtp_class, smtp = _smtp(offers_starttls=False)
    with patch.object(check.smtplib, "SMTP", smtp_class):
        check.send_email("s", "b", MAIL, ["to@y"])
    smtp_class.assert_called_once_with("relay.test", 2525, timeout=30)
    assert smtp.send_message.call_args[0][0]["From"] == "Bloom <noreply@test>"


def test_mail_is_encrypted_when_the_relay_offers_it():
    smtp_class, smtp = _smtp(offers_starttls=True)
    with patch.object(check.smtplib, "SMTP", smtp_class):
        check.send_email("s", "b", MAIL, ["to@y"])
    smtp.starttls.assert_called_once()
    assert smtp.method_calls.index(next(c for c in smtp.method_calls if c[0] == "starttls")) < \
        smtp.method_calls.index(next(c for c in smtp.method_calls if c[0] == "send_message"))


def test_mail_is_sent_plain_when_the_relay_does_not_offer_encryption():
    smtp_class, smtp = _smtp(offers_starttls=False)
    with patch.object(check.smtplib, "SMTP", smtp_class):
        check.send_email("s", "b", MAIL, ["to@y"])
    smtp.starttls.assert_not_called()
    smtp.send_message.assert_called_once()


def test_send_email_passes_relay_errors_up():
    with patch.object(check.smtplib, "SMTP", side_effect=smtplib.SMTPException("no")):
        with pytest.raises(smtplib.SMTPException):
            check.send_email("s", "b", MAIL, ["to@y"])


# --- the workflow -----------------------------------------------------------------------


@pytest.fixture(scope="module")
def wf():
    return yaml.safe_load(WORKFLOW.read_text())


def _on(wf):
    return wf.get("on") or wf.get(True)


def test_the_workflow_runs_weekly_and_by_hand(wf):
    assert _on(wf)["schedule"] == [{"cron": "17 16 * * 0"}]
    assert "send_test_email" in _on(wf)["workflow_dispatch"]["inputs"]


def test_the_workflow_runs_on_the_salk_network_runner_with_read_only_access(wf):
    (job,) = wf["jobs"].values()
    assert job["runs-on"] == ["self-hosted", "linux", "salk-network"]
    assert wf["permissions"] == {"contents": "read"}
    assert job["timeout-minutes"] <= 15


def test_the_workflow_checks_prod_only_and_uses_no_secrets():
    text = WORKFLOW.read_text()
    assert "--env-file .env.prod.defaults" in text
    assert ".env.staging.defaults" not in text
    assert "secrets." not in text
    assert "CERT_CHECK_FROM" not in text and "CERT_CHECK_SMTP_HOST" not in text
