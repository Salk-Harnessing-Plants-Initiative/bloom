"""Sends Bloom's email through Salk's relay, with the SMTP_* settings GoTrue's sign-in emails use.

The one place that talks to the relay: the run-finished email and the mail service both
send with send(). Without SMTP_HOST (dev, CI) configured() is false and callers send nothing.
"""

import os
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formataddr

SMTP_HOST = os.environ.get("SMTP_HOST") or None
SMTP_PORT = int(os.environ.get("SMTP_PORT") or 25)
SENDER = os.environ.get("SMTP_ADMIN_EMAIL") or "noreply@bloom.salk.edu"
SENDER_NAME = os.environ.get("SMTP_SENDER_NAME") or "Bloom"
SMTP_TIMEOUT_SECONDS = 15


def configured() -> bool:
    return SMTP_HOST is not None


def message(to: str, subject: str, body: str, reply_to: str = "") -> EmailMessage:
    """A plain-text message from Bloom's sender; replies go to `reply_to` when given."""
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = formataddr((SENDER_NAME, SENDER))
    msg["To"] = to
    if reply_to:
        msg["Reply-To"] = reply_to
    msg.set_content(body)
    return msg


def send(msg: EmailMessage) -> None:
    """Hands the message to the relay over STARTTLS; raises on any failure."""
    with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=SMTP_TIMEOUT_SECONDS) as smtp:
        smtp.starttls(context=ssl.create_default_context())
        smtp.send_message(msg)
