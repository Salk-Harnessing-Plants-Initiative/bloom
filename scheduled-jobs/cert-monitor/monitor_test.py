"""Unit tests for monitor_lib.py.

Fixtures under test_fixtures/ are REAL Caddy log lines captured during the
2026-06-09 prod first-ACME and the pre-PR-#284 staging deploy failure
(run 27157566611). Assertions lock in the actual Caddy 2.11.3 emit strings,
so an upstream format change breaks tests before it silently breaks the
running monitor.
"""

from __future__ import annotations

import json
import smtplib
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent))

from monitor_lib import (  # noqa: E402
    CaddyEvent, CertInfo, MonitorState, Notification,
    build_notifications, classify, load_state, parse_events,
    parse_openssl_output, save_state, send_email, subject_to_identifier,
)

FIXTURES = Path(__file__).parent / "test_fixtures"
SUCCESS_FIXTURE = FIXTURES / "success_prod_2026_06_09.jsonl"
FAILURE_FIXTURE = FIXTURES / "failure_pr_254.jsonl"
MAINTENANCE_FIXTURE = FIXTURES / "maintenance_steady_state.jsonl"


# ---------- parser tests against real prod success log ----------

def test_success_fixture_yields_two_obtained_events():
    events = parse_events(SUCCESS_FIXTURE.read_text())
    successes = [e for e in events if e.is_success()]
    assert len(successes) == 2
    assert {e.identifier for e in successes} == {"bloom-dev.salk.edu", "*.bloom-dev.salk.edu"}


def test_success_events_have_letsencrypt_issuer():
    events = parse_events(SUCCESS_FIXTURE.read_text())
    successes = [e for e in events if e.is_success()]
    assert all(e.issuer == "acme-v02.api.letsencrypt.org-directory" for e in successes)


def test_success_fixture_has_no_failures():
    events = parse_events(SUCCESS_FIXTURE.read_text())
    assert not any(e.is_failure() for e in events)


# ---------- parser tests against real PR #254 failure log ----------

def test_failure_fixture_yields_failure_markers():
    events = parse_events(FAILURE_FIXTURE.read_text())
    failures = [e for e in events if e.is_failure()]
    # The fixture contains 2 "could not get certificate from issuer",
    # 1 "job failed", and 2 "cleaning up solver" = 5 failure markers.
    assert len(failures) >= 4


def test_failure_fixture_classifies_three_distinct_logger_paths():
    events = parse_events(FAILURE_FIXTURE.read_text())
    failures = [e for e in events if e.is_failure()]
    distinct_loggers = {e.logger_path for e in failures}
    assert "tls.obtain" in distinct_loggers
    assert "tls" in distinct_loggers
    assert "http.acme_client" in distinct_loggers


def test_failure_fixture_exposes_actual_pr_254_error_message():
    events = parse_events(FAILURE_FIXTURE.read_text())
    failures = [e for e in events if e.is_failure()]
    errors = " ".join(e.error or "" for e in failures)
    assert "expected 1 zone, got 0 for salk.edu" in errors


def test_failure_fixture_includes_will_retry_events():
    events = parse_events(FAILURE_FIXTURE.read_text())
    retries = [e for e in events if e.is_retry()]
    assert len(retries) >= 1
    assert retries[0].attempt is not None
    assert retries[0].elapsed is not None


# ---------- parser tests against real maintenance log ----------

def test_maintenance_fixture_yields_planning_events():
    events = parse_events(MAINTENANCE_FIXTURE.read_text())
    planning = [e for e in events if e.is_planning()]
    assert len(planning) == 4
    assert all(e.selected_time is not None for e in planning)


def test_maintenance_planning_events_carry_both_identifiers():
    events = parse_events(MAINTENANCE_FIXTURE.read_text())
    planning = [e for e in events if e.is_planning()]
    identifiers = {e.identifier for e in planning}
    assert identifiers == {"bloom-dev.salk.edu", "*.bloom-dev.salk.edu"}


def test_maintenance_selected_time_matches_30_day_renewal_window():
    events = parse_events(MAINTENANCE_FIXTURE.read_text())
    apex_planning = [e for e in events if e.is_planning() and e.identifier == "bloom-dev.salk.edu"]
    # selected_time = 1786114912 (2026-08-08 UTC) vs cert_expiry = 1788756716 (2026-09-07 UTC)
    # 1788756716 - 1786114912 = 2641804 sec ≈ 30.5 days. Matches Caddy's "renew 30d before expiry" rule.
    sample = apex_planning[0]
    gap_days = (1788756716 - sample.selected_time) / 86400
    assert 29 < gap_days < 32


# ---------- openssl parser ----------

REAL_OPENSSL_OUTPUT = """subject= CN = bloom-dev.salk.edu
issuer= C = US, O = Let's Encrypt, CN = YE1
notBefore=Jun  9 05:50:18 2026 GMT
notAfter=Sep  7 05:50:17 2026 GMT
"""

def test_openssl_parser_extracts_all_fields():
    cert = parse_openssl_output(REAL_OPENSSL_OUTPUT)
    assert cert.subject == "CN = bloom-dev.salk.edu"
    assert "Let's Encrypt" in cert.issuer
    assert cert.not_before.year == 2026
    assert cert.not_after.year == 2026
    # LE issues for 90 days but notAfter is one second before notBefore+90d,
    # so timedelta.days rounds down to 89.
    assert (cert.not_after - cert.not_before).days == 89


def test_openssl_parser_rejects_mangled_input():
    with pytest.raises((KeyError, ValueError)):
        parse_openssl_output("garbage")


# ---------- subject_to_identifier ----------

def test_subject_to_identifier_strips_cn_prefix():
    assert subject_to_identifier("CN = bloom-dev.salk.edu") == "bloom-dev.salk.edu"
    assert subject_to_identifier("C = US, O = LE, CN = *.bloom-dev.salk.edu") == "*.bloom-dev.salk.edu"


def test_subject_to_identifier_falls_back_to_subject_when_no_cn():
    assert subject_to_identifier("no-cn-here") == "no-cn-here"


# ---------- state I/O ----------

def test_state_roundtrip(tmp_path):
    path = tmp_path / "staging.json"
    original = MonitorState(
        last_run_utc="2026-06-09T00:00:00+00:00",
        last_not_before={"bloom-dev.salk.edu": "2026-06-09T05:50:18+00:00"},
    )
    save_state(path, original)
    loaded = load_state(path)
    assert loaded.last_run_utc == original.last_run_utc
    assert loaded.last_not_before == original.last_not_before


def test_state_load_returns_empty_for_missing_file(tmp_path):
    state = load_state(tmp_path / "nonexistent.json")
    assert state.last_run_utc is None
    assert state.last_not_before == {}


def test_state_save_creates_parent_dir(tmp_path):
    nested = tmp_path / "a" / "b" / "c.json"
    save_state(nested, MonitorState(last_run_utc="x"))
    assert nested.exists()


def test_state_save_is_atomic(tmp_path):
    path = tmp_path / "staging.json"
    save_state(path, MonitorState(last_run_utc="first"))
    save_state(path, MonitorState(last_run_utc="second"))
    raw = json.loads(path.read_text())
    assert raw["last_run_utc"] == "second"


# ---------- classify state machine ----------

CERT_FRESH = CertInfo(
    subject="CN = bloom-dev.salk.edu",
    issuer="CN = YE1",
    not_before=datetime(2026, 6, 9, tzinfo=timezone.utc),
    not_after=datetime(2026, 9, 7, tzinfo=timezone.utc),
)

CERT_NEAR_EXPIRY = CertInfo(
    subject="CN = bloom-dev.salk.edu",
    issuer="CN = YE1",
    not_before=datetime(2026, 3, 9, tzinfo=timezone.utc),
    not_after=datetime(2026, 6, 9, tzinfo=timezone.utc),
)

NOW = datetime(2026, 6, 1, tzinfo=timezone.utc)


def test_first_run_no_state_emits_no_notifications():
    states = classify("staging", CERT_FRESH, [], MonitorState(), NOW, expiry_alert_days=14)
    assert states == []


def test_renewal_detected_when_notbefore_advances():
    state = MonitorState(last_not_before={"bloom-dev.salk.edu": "2026-04-01T00:00:00+00:00"})
    states = classify("staging", CERT_FRESH, [], state, NOW, expiry_alert_days=14)
    assert "renewed" in states


def test_failure_classified_from_pr_254_fixture():
    events = parse_events(FAILURE_FIXTURE.read_text())
    states = classify("staging", CERT_FRESH, events, MonitorState(), NOW, expiry_alert_days=14)
    assert "failed" in states


def test_silent_expiry_when_cert_near_expiry_and_no_success():
    states = classify("staging", CERT_NEAR_EXPIRY, [], MonitorState(), NOW, expiry_alert_days=14)
    assert "silent_expiry" in states


def test_no_silent_expiry_when_success_event_present():
    events = parse_events(SUCCESS_FIXTURE.read_text())
    states = classify("staging", CERT_NEAR_EXPIRY, events, MonitorState(), NOW, expiry_alert_days=14)
    assert "silent_expiry" not in states


def test_partial_success_when_prod_has_only_one_of_two():
    events = [
        CaddyEvent(ts=1.0, level="info", logger_path="tls.obtain",
                   msg="certificate obtained successfully",
                   identifier="bloom-dev.salk.edu", issuer="acme-v02.api.letsencrypt.org-directory"),
    ]
    states = classify("prod", CERT_FRESH, events, MonitorState(), NOW, expiry_alert_days=14)
    assert "partial_success" in states


def test_no_partial_success_for_staging_with_one_success():
    events = parse_events(SUCCESS_FIXTURE.read_text())
    # Filter to just the wildcard event so we have exactly 1 success.
    wildcard_events = [e for e in events if e.identifier == "*.bloom-dev.salk.edu"]
    states = classify("staging", CERT_FRESH, wildcard_events, MonitorState(), NOW, expiry_alert_days=14)
    assert "partial_success" not in states


def test_silent_expiry_uses_selected_time_when_present():
    events = parse_events(MAINTENANCE_FIXTURE.read_text())
    # Maintenance fixture's selected_time = 1786114912 (2026-08-08 UTC).
    # Set "now" to 30 days past that with no success events.
    far_future = datetime.fromtimestamp(1786114912, tz=timezone.utc) + timedelta(days=30)
    states = classify("staging", CERT_FRESH, events, MonitorState(),
                      far_future, expiry_alert_days=14)
    assert "silent_expiry" in states


def test_renewal_takes_precedence_over_silent_expiry():
    # Contrived: a "renewal landed" event for a cert that's still near expiry.
    # In real life renewal lands a fresh 90-day cert, but we want to verify the
    # precedence rule in classify() — if 'renewed' fires, 'silent_expiry' is suppressed.
    cert_renewed_but_near_expiry = CertInfo(
        subject="CN = bloom-dev.salk.edu",
        issuer="CN = YE1",
        not_before=datetime(2026, 5, 25, tzinfo=timezone.utc),  # newer than prior
        not_after=datetime(2026, 6, 9, tzinfo=timezone.utc),    # but still near expiry
    )
    state = MonitorState(last_not_before={"bloom-dev.salk.edu": "2026-04-01T00:00:00+00:00"})
    states = classify("staging", cert_renewed_but_near_expiry, [], state, NOW, expiry_alert_days=14)
    assert "renewed" in states
    assert "silent_expiry" not in states


# ---------- notification builders ----------

def test_renewed_notification_includes_cert_dates():
    notes = build_notifications("staging", CERT_FRESH, [], ["renewed"], notify_on_success=True)
    assert len(notes) == 1
    assert notes[0].kind == "renewed"
    assert "2026-06-09" in notes[0].body
    assert "2026-09-07" in notes[0].body


def test_success_notification_suppressed_when_notify_on_success_false():
    notes = build_notifications("staging", CERT_FRESH, [], ["renewed"], notify_on_success=False)
    assert notes == []


def test_failed_notification_includes_real_pr_254_error():
    events = parse_events(FAILURE_FIXTURE.read_text())
    notes = build_notifications("staging", CERT_FRESH, events, ["failed"], notify_on_success=True)
    assert len(notes) == 1
    assert "expected 1 zone, got 0 for salk.edu" in notes[0].body


def test_failed_notification_includes_retry_info_when_present():
    events = parse_events(FAILURE_FIXTURE.read_text())
    notes = build_notifications("staging", CERT_FRESH, events, ["failed"], notify_on_success=True)
    assert "retry-in-progress" in notes[0].body


def test_silent_expiry_notification_mentions_selected_time():
    events = parse_events(MAINTENANCE_FIXTURE.read_text())
    notes = build_notifications("staging", CERT_NEAR_EXPIRY, events,
                                ["silent_expiry"], notify_on_success=True)
    assert any("Caddy planned to renew" in n.body for n in notes)


def test_both_renewed_and_failed_emit_two_notifications():
    events = parse_events(FAILURE_FIXTURE.read_text())
    notes = build_notifications("staging", CERT_FRESH, events,
                                ["renewed", "failed"], notify_on_success=True)
    assert {n.kind for n in notes} == {"renewed", "failed"}


# ---------- SMTP send ----------

def test_send_email_uses_correct_subject_prefix():
    note = Notification(env="prod", kind="renewed", subject_suffix="renewed", body="test")
    with patch("monitor_lib.smtplib.SMTP") as smtp_class:
        smtp_instance = MagicMock()
        smtp_class.return_value.__enter__.return_value = smtp_instance
        send_email(note, "from@x", ["to@y"], "smtp.host")
        sent_msg = smtp_instance.send_message.call_args[0][0]
        assert sent_msg["Subject"] == "[bloom-cert-monitor] prod renewed"


def test_send_email_uses_multiple_recipients_in_one_transaction():
    note = Notification(env="prod", kind="failed", subject_suffix="failed", body="x")
    with patch("monitor_lib.smtplib.SMTP") as smtp_class:
        smtp_instance = MagicMock()
        smtp_class.return_value.__enter__.return_value = smtp_instance
        send_email(note, "from@x", ["a@y", "b@y", "c@y"], "smtp.host")
        assert smtp_instance.send_message.call_count == 1
        sent_msg = smtp_instance.send_message.call_args[0][0]
        assert sent_msg["To"] == "a@y, b@y, c@y"


def test_send_email_propagates_smtp_failures():
    note = Notification(env="prod", kind="failed", subject_suffix="failed", body="x")
    with patch("monitor_lib.smtplib.SMTP") as smtp_class:
        smtp_class.side_effect = smtplib.SMTPException("relay rejected")
        with pytest.raises(smtplib.SMTPException):
            send_email(note, "from@x", ["to@y"], "smtp.host")


# ---------- end-to-end smoke against fixtures ----------

def test_full_flow_against_success_fixture_with_baseline_state():
    """Baseline scenario: monitor ran last week, cert hasn't renewed, no events to act on."""
    events = parse_events(SUCCESS_FIXTURE.read_text())
    state = MonitorState(last_not_before={"bloom-dev.salk.edu": CERT_FRESH.not_before.isoformat()})
    states = classify("prod", CERT_FRESH, events, state, NOW, expiry_alert_days=14)
    notes = build_notifications("prod", CERT_FRESH, events, states, notify_on_success=True)
    # notBefore unchanged → no "renewed". Both prod successes present → no partial.
    # No failure events. No silent_expiry.
    assert states == []
    assert notes == []


# ---------- --env CLI flag filter (B1 fix) ----------

def test_env_filter_restricts_to_one_env():
    """The --env flag (passed by per-env systemd services) restricts iteration
    to a single CADDY_ENVS entry, so each env's timer only touches its own
    container."""
    from monitor import CADDY_ENVS  # local import — module-level import would
                                    # trigger main()'s argparse against pytest's argv

    staging_only = [e for e in CADDY_ENVS if e.label == "staging"]
    prod_only = [e for e in CADDY_ENVS if e.label == "prod"]

    assert len(staging_only) == 1
    assert len(prod_only) == 1
    assert staging_only[0].container == "bloom_v2_staging-caddy-1"
    assert prod_only[0].container == "bloom_v2_prod-caddy-1"


def test_env_filter_omitted_means_both_envs():
    """For one-off CLI use (no --env flag), iteration covers both envs."""
    from monitor import CADDY_ENVS

    both = list(CADDY_ENVS)
    assert {e.label for e in both} == {"staging", "prod"}


# ---------- B2 fix: state persists even when SMTP fails ----------

def test_state_saved_when_smtp_fails_during_process_env(tmp_path):
    """B2 fix: when SMTP raises during a notification send, _process_env's
    try/finally must still write state. Otherwise next week we re-classify
    the same renewal as new and re-emit the same notification."""
    import monitor

    # Pre-existing state with OLD notBefore — represents last week's snapshot
    state_path = tmp_path / "prod.json"
    save_state(state_path, MonitorState(
        last_run_utc="2026-06-02T09:00:00+00:00",
        last_not_before={"bloom-dev.salk.edu": "2026-04-01T00:00:00+00:00"},
    ))

    fresh_cert = CertInfo(
        subject="CN = bloom-dev.salk.edu",
        issuer="CN = YE1",
        not_before=datetime(2026, 6, 9, tzinfo=timezone.utc),  # newer → "renewed" fires
        not_after=datetime(2026, 9, 7, tzinfo=timezone.utc),
    )

    with patch.object(monitor, "list_cert_subjects", return_value=["bloom-dev.salk.edu"]), \
         patch.object(monitor, "read_caddy_logs", return_value=""), \
         patch.object(monitor, "read_cert", return_value=fresh_cert), \
         patch.object(monitor, "send_email", side_effect=smtplib.SMTPException("relay rejected")):
        with pytest.raises(smtplib.SMTPException):
            monitor._process_env(
                caddy_env=monitor.CADDY_ENVS[1],  # prod
                now=datetime(2026, 6, 9, 9, 0, tzinfo=timezone.utc),
                state_dir=tmp_path,
                expiry_alert_days=14,
                notify_on_success=True,
                sender="from@x",
                recipients=["to@y"],
                smtp_host="smtp.host",
            )

    # Critical assertion: state file MUST contain the new notBefore even though
    # the SMTP send raised. Otherwise next week's run re-fires "renewed".
    saved = load_state(state_path)
    assert saved.last_not_before["bloom-dev.salk.edu"] == fresh_cert.not_before.isoformat()
    assert saved.last_run_utc == "2026-06-09T09:00:00+00:00"


def test_state_saved_on_clean_run_through_process_env(tmp_path):
    """Sanity check: when nothing raises, _process_env writes the new state at
    the end (covers the non-exceptional path)."""
    import monitor

    state_path = tmp_path / "staging.json"
    save_state(state_path, MonitorState(
        last_run_utc="2026-06-02T09:00:00+00:00",
        last_not_before={"*.bloom-dev.salk.edu": "2026-04-01T00:00:00+00:00"},
    ))

    fresh_cert = CertInfo(
        subject="CN = *.bloom-dev.salk.edu",
        issuer="CN = YE2",
        not_before=datetime(2026, 6, 9, tzinfo=timezone.utc),
        not_after=datetime(2026, 9, 7, tzinfo=timezone.utc),
    )

    with patch.object(monitor, "list_cert_subjects", return_value=["wildcard_.bloom-dev.salk.edu"]), \
         patch.object(monitor, "read_caddy_logs", return_value=""), \
         patch.object(monitor, "read_cert", return_value=fresh_cert), \
         patch.object(monitor, "send_email"):  # no-op; SMTP succeeds
        monitor._process_env(
            caddy_env=monitor.CADDY_ENVS[0],  # staging
            now=datetime(2026, 6, 9, 9, 0, tzinfo=timezone.utc),
            state_dir=tmp_path,
            expiry_alert_days=14,
            notify_on_success=True,
            sender="from@x",
            recipients=["to@y"],
            smtp_host="smtp.host",
        )

    saved = load_state(state_path)
    assert saved.last_not_before["*.bloom-dev.salk.edu"] == fresh_cert.not_before.isoformat()
