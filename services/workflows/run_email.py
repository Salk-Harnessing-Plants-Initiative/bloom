"""
The email a scientist gets when an RNA-seq run they started finishes.

The status poller calls notify() in the pass that records the run's final status. It looks
the requester's email up with rnaseq_run_requesters and sends one plain-text message through
Salk's mail relay (the SMTP_* settings GoTrue's sign-in emails use): the outcome and its
message, a link to the run page and, for a run on an S3 folder, a reminder to remove Bloom's
access to it if it was shared. Without SMTP_HOST (dev) nothing is sent. A failure is logged
and never changes the run.
"""

import logging
import os
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formataddr

logger = logging.getLogger(__name__)

REQUESTERS_FN = "rnaseq_run_requesters"
SMTP_HOST = os.environ.get("SMTP_HOST") or None
SMTP_PORT = int(os.environ.get("SMTP_PORT") or 25)
SENDER = os.environ.get("SMTP_ADMIN_EMAIL") or "noreply@bloom.salk.edu"
SENDER_NAME = os.environ.get("SMTP_SENDER_NAME") or "Bloom"
SITE_URL = (os.environ.get("SITE_URL") or "").rstrip("/")
SMTP_TIMEOUT_SECONDS = 15

# The final statuses, and how the subject puts each.
OUTCOMES = {
    "succeeded": "finished",
    "failed": "failed",
    "skipped": "was already done",
}


def run_page(run_id) -> str:
    return f"{SITE_URL}/app/timeline/rnaseq/{run_id}"


def build(run: dict, status, to: str) -> EmailMessage:
    """The message for a finished run; `status` is its recorded RunStatus."""
    params = run.get("params") or {}
    sample = params.get("sample") or "your sample"
    outcome = OUTCOMES[status.status]
    lines = [
        f"Your Cell Ranger run on {sample} (reference {params.get('reference')}) {outcome}.",
        "",
    ]
    if status.message:
        lines += [status.message, ""]
    lines += [f"See the run: {run_page(run['id'])}"]
    if params.get("fastq_url"):
        lines += [
            "",
            f"If you shared {params['fastq_url']} with Bloom's AWS user so this run could read "
            "it, you can remove that access now.",
        ]
    message = EmailMessage()
    message["Subject"] = f"Bloom: your Cell Ranger run on {sample} {outcome}"
    message["From"] = formataddr((SENDER_NAME, SENDER))
    message["To"] = to
    message.set_content("\n".join(lines) + "\n")
    return message


def _requester(client, run_id) -> str | None:
    rows = client.rpc(REQUESTERS_FN, {"p_run_ids": [run_id]}).execute().data or []
    return next(
        (r["email"] for r in rows if r.get("run_id") == run_id and r.get("email")), None
    )


def _send(message: EmailMessage) -> None:
    with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=SMTP_TIMEOUT_SECONDS) as smtp:
        smtp.starttls(context=ssl.create_default_context())
        smtp.send_message(message)


def notify(client, run: dict, status) -> None:
    """Emails the run's requester that it finished. Never raises."""
    if SMTP_HOST is None or status.status not in OUTCOMES:
        return
    try:
        to = _requester(client, run["id"])
        if to is None:
            logger.warning(
                "run_email: run %s has no requester email; nothing sent", run["id"]
            )
            return
        _send(build(run, status, to))
    except Exception as exc:  # noqa: BLE001 - an email must never change or stop the run
        logger.warning(
            "run_email: run %s's email was not sent: %s", run["id"], type(exc).__name__
        )
        return
    logger.info(
        "run_email: run %s's requester was emailed that it %s", run["id"], status.status
    )
