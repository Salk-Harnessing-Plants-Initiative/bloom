"""Unit tests for the run-finished email: what it says, how it's sent through Salk's relay, the
requester lookup, and that a failure never reaches the run. No real mail is sent."""

import logging
from types import SimpleNamespace

import pytest

import rnaseq_status_poller as poller
import run_email
from rnaseq_status import RunStatus

RUN = {
    "id": 42,
    "workflow_type": "scrna-cellranger",
    "params": {"sample": "col0", "reference": "tair10"},
    "run_key": "col0__tair10__u",
    "status": "running",
    "argo_workflow_name": "wf-42",
}
FOLDER_RUN = {**RUN, "params": {**RUN["params"], "fastq_url": "s3://lab-private/col0/"}}
DONE = RunStatus(
    "succeeded", "cleanup", {}, 0, "Finished: results in s3://bloomv2-workflows/x/"
)


class FakeClient:
    """Answers rnaseq_run_requesters with `emails` (run id → email)."""

    def __init__(self, emails=None, fail=False):
        self.emails = emails if emails is not None else {42: "alice@salk.edu"}
        self.fail = fail
        self.rpcs = []

    def rpc(self, name, params):
        self.rpcs.append((name, params))
        client = self

        class _Call:
            def execute(self):
                if client.fail:
                    raise RuntimeError("database down")
                data = [
                    {"run_id": i, "email": client.emails[i]}
                    for i in params["p_run_ids"]
                    if i in client.emails
                ]
                return type("R", (), {"data": data})()

        return _Call()


class FakeSMTP:
    sent: list = []
    tls: list = []
    fail = False

    def __init__(self, host, port, timeout):
        self.where = (host, port, timeout)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def starttls(self, context):
        FakeSMTP.tls.append(context)

    def send_message(self, message):
        if FakeSMTP.fail:
            raise OSError("relay refused")
        FakeSMTP.sent.append((self.where, message))


@pytest.fixture
def relay(monkeypatch):
    FakeSMTP.sent, FakeSMTP.tls, FakeSMTP.fail = [], [], False
    monkeypatch.setattr(run_email, "SMTP_HOST", "neoemex1.salk.edu")
    monkeypatch.setattr(run_email, "SMTP_PORT", 25)
    monkeypatch.setattr(run_email, "SITE_URL", "https://bloom.salk.edu")
    monkeypatch.setattr(run_email.smtplib, "SMTP", FakeSMTP)
    return FakeSMTP


# --------------------------------------------------------------------------- #
# What it says
# --------------------------------------------------------------------------- #


def test_a_finished_run_says_so_with_its_message_and_page(relay):
    message = run_email.build(RUN, DONE, "alice@salk.edu")
    body = message.get_content()
    assert message["Subject"] == "Bloom: your Cell Ranger run on col0 finished"
    assert message["To"] == "alice@salk.edu"
    assert message["From"] == "Bloom <noreply@bloom.salk.edu>"
    assert "Your Cell Ranger run on col0 (reference tair10) finished." in body
    assert "Finished: results in s3://bloomv2-workflows/x/" in body
    assert "See the run: https://bloom.salk.edu/app/timeline/rnaseq/42" in body
    assert "remove that access" not in body


def test_a_folder_run_reminds_the_scientist_to_remove_blooms_access(relay):
    body = run_email.build(FOLDER_RUN, DONE, "alice@salk.edu").get_content()
    assert "If you shared s3://lab-private/col0/ with Bloom's reader" in body
    assert "you can remove that access now" in body


@pytest.mark.parametrize(
    "status, words",
    [("failed", "failed"), ("skipped", "was already done"), ("succeeded", "finished")],
)
def test_the_subject_names_the_outcome(relay, status, words):
    message = run_email.build(RUN, RunStatus(status, message="m"), "alice@salk.edu")
    assert message["Subject"] == f"Bloom: your Cell Ranger run on col0 {words}"


# --------------------------------------------------------------------------- #
# Sending
# --------------------------------------------------------------------------- #


def test_it_is_sent_to_the_requester_through_the_relay_over_tls(relay):
    client = FakeClient()
    run_email.notify(client, FOLDER_RUN, DONE)
    assert client.rpcs == [("rnaseq_run_requesters", {"p_run_ids": [42]})]
    assert len(relay.sent) == 1 and len(relay.tls) == 1
    where, message = relay.sent[0]
    assert where == ("neoemex1.salk.edu", 25, run_email.SMTP_TIMEOUT_SECONDS)
    assert message["To"] == "alice@salk.edu"


def test_nothing_is_sent_without_a_relay(monkeypatch, relay):
    monkeypatch.setattr(run_email, "SMTP_HOST", None)
    client = FakeClient()
    run_email.notify(client, RUN, DONE)
    assert client.rpcs == [] and relay.sent == []


def test_a_run_still_going_gets_no_email(relay):
    run_email.notify(FakeClient(), RUN, RunStatus("running", "count"))
    assert relay.sent == []


def test_a_run_without_a_requester_email_is_logged_not_sent(relay, caplog):
    with caplog.at_level(logging.WARNING, logger="run_email"):
        run_email.notify(FakeClient(emails={}), RUN, DONE)
    assert relay.sent == [] and "no requester email" in caplog.text


@pytest.mark.parametrize("broken", ["relay", "lookup"])
def test_a_failure_is_logged_and_never_raised(relay, caplog, broken):
    relay.fail = broken == "relay"
    with caplog.at_level(logging.WARNING, logger="run_email"):
        run_email.notify(FakeClient(fail=broken == "lookup"), RUN, DONE)
    assert "run 42's email was not sent" in caplog.text
    assert "alice@salk.edu" not in caplog.text


# --------------------------------------------------------------------------- #
# The poller emails once, when the run finishes
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("changed, emailed", [(True, 1), (False, 0)])
def test_the_poller_emails_only_on_the_update_that_finished_the_run(
    monkeypatch, changed, emailed
):
    sent = []
    monkeypatch.setattr(
        poller, "get_workflow", lambda name: {"metadata": {"name": name}}
    )
    monkeypatch.setattr(poller, "_record", lambda client, run_id, status: changed)
    monkeypatch.setattr(poller, "_register_sample", lambda *a: None)
    monkeypatch.setitem(
        poller.WORKFLOW_TYPES,
        "scrna-cellranger",
        SimpleNamespace(read_status=lambda wf, run: DONE),
    )
    monkeypatch.setattr(
        poller.run_email, "notify", lambda c, run, status: sent.append(run["id"])
    )
    poller.poll_run(object(), RUN)
    assert sent == [42] * emailed
