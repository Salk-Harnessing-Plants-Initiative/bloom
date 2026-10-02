"""The weekly TLS certificate expiry check and its workflow."""

from __future__ import annotations

import smtplib
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scheduled-jobs" / "cert-expiry-check"))

import check  # noqa: E402

NOW = datetime(2026, 10, 2, 16, 0, tzinfo=timezone.utc)
WORKFLOW = REPO / ".github" / "workflows" / "cert-expiry-check.yml"


def _expiring_in(days):
    return lambda host, port: NOW + timedelta(days=days, hours=1)


# --- which sites are checked ---------------------------------------------------------


def test_prod_checks_the_three_sites_caddy_serves_on_443():
    assert check.served_hosts(REPO / ".env.prod.defaults") == [
        ("bloom.salk.edu", 443), ("studio.bloom.salk.edu", 443), ("minio.bloom.salk.edu", 443)]


def test_staging_checks_its_sites_on_8443():
    assert check.served_hosts(REPO / ".env.staging.defaults") == [
        ("staging.bloom.salk.edu", 8443), ("staging-studio.bloom.salk.edu", 8443),
        ("staging-minio.bloom.salk.edu", 8443)]


def test_an_env_file_without_a_main_domain_is_refused(tmp_path):
    f = tmp_path / ".env.x.defaults"
    f.write_text("CADDY_HTTPS_LISTEN_PORT=443\n")
    with pytest.raises(ValueError, match="DOMAIN_MAIN"):
        check.served_hosts(f)


# --- the decision ---------------------------------------------------------------------


def test_a_cert_with_plenty_of_time_is_fine():
    (r,) = check.check([("bloom.salk.edu", 443)], NOW, warn_days=21, fetch=_expiring_in(60))
    assert r.ok and r.days_left == 60


def test_a_cert_under_the_warning_line_is_a_problem():
    (r,) = check.check([("bloom.salk.edu", 443)], NOW, warn_days=21, fetch=_expiring_in(20))
    assert not r.ok and r.days_left == 20


def test_a_site_that_cannot_be_checked_is_a_problem_not_a_crash():
    def broken(host, port):
        raise check.CertProblem("certificate has expired")

    (r,) = check.check([("bloom.salk.edu", 443)], NOW, warn_days=21, fetch=broken)
    assert not r.ok and r.problem == "certificate has expired"


# --- the email --------------------------------------------------------------------------


def test_the_email_names_each_problem_site_and_what_to_do():
    results = check.check([("bloom.salk.edu", 443), ("staging.bloom.salk.edu", 8443)], NOW,
                          warn_days=21, fetch=lambda h, p: NOW + timedelta(days=5 if p == 443 else 80))
    subject, body = check.build_alert(results, warn_days=21)
    assert "bloom.salk.edu" in subject and "5 days" in subject
    assert "bloom.salk.edu:443" in body and "expires in 5 days" in body
    assert "staging.bloom.salk.edu" not in body, "healthy sites are left out of the alert"
    assert "docker logs" in body and "_acme-challenge.bloom.salk.edu" in body


def test_an_unreachable_site_is_described_in_the_email():
    def broken(host, port):
        raise check.CertProblem("connection refused")

    subject, body = check.build_alert(check.check([("bloom.salk.edu", 443)], NOW, 21, fetch=broken), 21)
    assert "could not be checked" in subject
    assert "connection refused" in body


# --- the command ------------------------------------------------------------------------


@pytest.fixture
def config(monkeypatch, tmp_path):
    monkeypatch.setenv("CERT_CHECK_RECIPIENTS", "a@salk.edu, b@salk.edu")
    monkeypatch.setenv("CERT_CHECK_SMTP_HOST", "relay.test")
    monkeypatch.setenv("CERT_CHECK_FROM", "bloom-cert-check@test")
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(tmp_path / "summary.md"))
    sent = []
    monkeypatch.setattr(check, "send_email", lambda *a: sent.append(a))
    return sent, tmp_path / "summary.md"


def _run(*args, days=60):
    with patch.object(check, "fetch_expiry", _expiring_in(days)), \
         patch.object(check, "utc_now", return_value=NOW):
        return check.main(["--env-file", str(REPO / ".env.prod.defaults"), *args])


def test_all_fine_sends_no_email_and_passes(config):
    sent, summary = config
    assert _run() == 0
    assert sent == []
    assert "bloom.salk.edu" in summary.read_text()


def test_a_problem_emails_the_team_and_fails_the_run(config):
    sent, _ = config
    assert _run(days=10) == 1
    (args,) = sent
    subject, body, sender, recipients, relay = args
    assert recipients == ["a@salk.edu", "b@salk.edu"] and relay == "relay.test"
    assert "10 days" in subject


def test_a_relay_that_refuses_still_fails_the_run(config, monkeypatch):
    def refuse(*a):
        raise ConnectionRefusedError("relay down")

    monkeypatch.setattr(check, "send_email", refuse)
    assert _run(days=10) == 2


def test_the_test_email_option_sends_one_email_and_checks_nothing(config):
    sent, _ = config
    with patch.object(check, "fetch_expiry", side_effect=AssertionError("no check in test mode")):
        assert check.main(["--test-email"]) == 0
    assert len(sent) == 1 and "test" in sent[0][0].lower()


def test_no_recipients_refuses_to_run(monkeypatch):
    monkeypatch.delenv("CERT_CHECK_RECIPIENTS", raising=False)
    assert check.main(["--env-file", str(REPO / ".env.prod.defaults")]) == 1


def test_send_email_uses_the_relay_on_port_25():
    with patch.object(check.smtplib, "SMTP") as smtp:
        check.send_email("s", "b", "from@x", ["to@y"], "relay.test")
    smtp.assert_called_once_with("relay.test", 25, timeout=30)


def test_send_email_passes_relay_errors_up():
    with patch.object(check.smtplib, "SMTP", side_effect=smtplib.SMTPException("no")):
        with pytest.raises(smtplib.SMTPException):
            check.send_email("s", "b", "from@x", ["to@y"], "relay.test")


# --- the workflow -----------------------------------------------------------------------


@pytest.fixture(scope="module")
def wf():
    return yaml.safe_load(WORKFLOW.read_text())


def _on(wf):
    return wf.get("on") or wf.get(True)


def test_the_workflow_runs_weekly_and_by_hand(wf):
    assert len(_on(wf)["schedule"]) == 1
    assert "send_test_email" in _on(wf)["workflow_dispatch"]["inputs"]


def test_the_workflow_runs_on_the_salk_network_runner_with_read_only_access(wf):
    (job,) = wf["jobs"].values()
    assert job["runs-on"] == ["self-hosted", "linux", "salk-network"]
    assert wf["permissions"] == {"contents": "read"}
    assert job["timeout-minutes"] <= 15


def test_the_workflow_checks_both_environments_and_uses_no_secrets():
    text = WORKFLOW.read_text()
    assert ".env.prod.defaults" in text and ".env.staging.defaults" in text
    assert "secrets." not in text


def test_the_tests_never_open_a_real_connection():
    with patch.object(check.socket, "create_connection", side_effect=AssertionError("network used")):
        with patch.object(check, "fetch_expiry", _expiring_in(60)):
            (r,) = check.check([("bloom.salk.edu", 443)], NOW, warn_days=21)
    assert r.ok
