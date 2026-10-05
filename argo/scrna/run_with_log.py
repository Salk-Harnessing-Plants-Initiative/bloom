#!/usr/bin/env python3
"""run-with-log: run a pipeline step and keep its log in Bloom's storage.

Usage: run-with-log --path scrna/<workflow name>/<step>.log -- <command> [args...]

Runs the command, passes everything it prints through to the pod's own output, saves a copy,
and uploads that copy to the `run-logs` bucket every RUN_LOG_INTERVAL seconds (30) while it
grows, and once more when the command ends. Exits with the command's exit code. Uploads never
fail the step: a failed upload is noted on stderr and tried again at the next interval.

Signs in to Bloom with bloomctl's credentials file (BLOOM_API_URL, BLOOM_ANON_KEY,
BLOOM_EMAIL, BLOOM_PASSWORD) at BLOOM_CREDENTIALS (/etc/bloom/credentials.txt). Without it,
the command runs and nothing is uploaded. The token and password are never printed.
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

BUCKET = "run-logs"
DEFAULT_CREDENTIALS = "/etc/bloom/credentials.txt"
DEFAULT_INTERVAL_SECONDS = 30.0
# The bucket refuses files over 50 MB; keep the end of a longer log, where errors are.
MAX_UPLOAD_BYTES = 45 * 1024 * 1024
HTTP_TIMEOUT_SECONDS = 20
# Sign in again this long before the token expires.
TOKEN_MARGIN_SECONDS = 120
CHUNK_BYTES = 64 * 1024
# The last upload, when the command has ended, is tried this many times.
FINAL_ATTEMPTS = 3
FINAL_RETRY_SECONDS = 2.0


def _note(message: str) -> None:
    print(f"run-with-log: {message}", file=sys.stderr, flush=True)


def read_credentials(path: str) -> dict[str, str] | None:
    """bloomctl's dotenv credentials, or None if the file is missing or incomplete."""
    try:
        lines = open(path, encoding="utf-8").read().splitlines()
    except OSError:
        return None
    values: dict[str, str] = {}
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip().removeprefix("export ").strip()] = value.strip().strip("'\"")
    keys = ("BLOOM_API_URL", "BLOOM_ANON_KEY", "BLOOM_EMAIL", "BLOOM_PASSWORD")
    if not all(values.get(k) for k in keys):
        return None
    return {k: values[k] for k in keys}


class Uploader:
    """Uploads one log file to run-logs, signing in when needed."""

    def __init__(self, credentials: dict[str, str], object_path: str):
        self.api = credentials["BLOOM_API_URL"].rstrip("/")
        self.anon_key = credentials["BLOOM_ANON_KEY"]
        self.email = credentials["BLOOM_EMAIL"]
        self.password = credentials["BLOOM_PASSWORD"]
        self.object_path = object_path
        self.token: str | None = None
        self.token_expires = 0.0

    def _request(self, url: str, body: bytes, headers: dict[str, str]) -> bytes:
        request = urllib.request.Request(url, data=body, headers=headers, method="POST")
        with urllib.request.urlopen(request, timeout=HTTP_TIMEOUT_SECONDS) as response:
            return response.read()

    def _sign_in(self) -> None:
        body = json.dumps({"email": self.email, "password": self.password}).encode()
        reply = json.loads(
            self._request(
                f"{self.api}/auth/v1/token?grant_type=password",
                body,
                {"apikey": self.anon_key, "Content-Type": "application/json"},
            )
        )
        self.token = reply["access_token"]
        self.token_expires = time.time() + float(reply.get("expires_in", 3600))

    def upload(self, data: bytes) -> None:
        """Upload `data` in place of the stored log. Raises on failure."""
        if (
            self.token is None
            or time.time() > self.token_expires - TOKEN_MARGIN_SECONDS
        ):
            self._sign_in()
        url = f"{self.api}/storage/v1/object/{BUCKET}/{urllib.parse.quote(self.object_path)}"
        try:
            self._send(url, data)
        except urllib.error.HTTPError as exc:
            if exc.code not in (401, 403):
                raise
            # The token may have been revoked or expired early: sign in once more.
            self._sign_in()
            self._send(url, data)

    def _send(self, url: str, data: bytes) -> None:
        self._request(
            url,
            data,
            {
                "Authorization": f"Bearer {self.token}",
                "apikey": self.anon_key,
                "Content-Type": "text/plain",
                "x-upsert": "true",
            },
        )


def _describe(exc: BaseException) -> str:
    """A failure without anything secret in it: the HTTP status, or the exception's type."""
    if isinstance(exc, urllib.error.HTTPError):
        return f"HTTP {exc.code}"
    if isinstance(exc, urllib.error.URLError):
        return f"can't reach Bloom ({type(exc.reason).__name__})"
    return type(exc).__name__


def _read_tail(path: str) -> bytes:
    size = os.path.getsize(path)
    with open(path, "rb") as f:
        if size <= MAX_UPLOAD_BYTES:
            return f.read()
        f.seek(size - MAX_UPLOAD_BYTES)
        dropped = size - MAX_UPLOAD_BYTES
        return (
            f"[run-with-log: the first {dropped} bytes were left out]\n".encode()
            + f.read()
        )


class LogSync:
    """Uploads the log file whenever it has changed, on a timer and at the end."""

    def __init__(self, uploader: Uploader | None, log_file: str, interval: float):
        self.uploader = uploader
        self.log_file = log_file
        self.interval = interval
        self.sent_size = -1
        self.stop = threading.Event()
        self.lock = threading.Lock()
        self.failed = False

    def sync(self) -> None:
        if self.uploader is None:
            return
        with self.lock:
            size = os.path.getsize(self.log_file)
            if size == self.sent_size:
                return
            try:
                self.uploader.upload(_read_tail(self.log_file))
                self.sent_size = size
                if self.failed:
                    _note("uploads are working again")
                self.failed = False
            except Exception as exc:  # noqa: BLE001 - an upload must never fail the step
                if not self.failed:
                    _note(f"couldn't upload the log ({_describe(exc)}); will try again")
                self.failed = True

    def run(self) -> None:
        while not self.stop.wait(self.interval):
            self.sync()

    def finish(self) -> None:
        """The last upload, tried a few times: it is the one with the whole log."""
        for attempt in range(FINAL_ATTEMPTS):
            self.sync()
            if not self.failed or self.uploader is None:
                return
            if attempt < FINAL_ATTEMPTS - 1:
                time.sleep(FINAL_RETRY_SECONDS)
        _note("the end of the log wasn't uploaded")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="run-with-log", description=__doc__.split("\n")[0]
    )
    parser.add_argument(
        "--path", required=True, help="where in run-logs to keep the log"
    )
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    if not command:
        parser.error("give the command to run after --")

    credentials = read_credentials(
        os.environ.get("BLOOM_CREDENTIALS", DEFAULT_CREDENTIALS)
    )
    uploader = Uploader(credentials, args.path) if credentials else None
    if uploader is None:
        _note("no Bloom credentials, so the log isn't uploaded")
    else:
        _note(f"the log is kept at {BUCKET}/{args.path}")

    interval = float(os.environ.get("RUN_LOG_INTERVAL", DEFAULT_INTERVAL_SECONDS))
    log = tempfile.NamedTemporaryFile(prefix="run-log-", suffix=".log", delete=False)
    sync = LogSync(uploader, log.name, interval)
    timer = threading.Thread(target=sync.run, daemon=True)
    timer.start()

    try:
        child = subprocess.Popen(
            command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT
        )
    except OSError as exc:
        message = f"run-with-log: can't run {command[0]}: {exc.strerror}\n".encode()
        log.write(message)
        log.close()
        sys.stderr.buffer.write(message)
        sync.stop.set()
        sync.finish()
        return 127

    # Pass a stop request on to the command, so it can clean up; its output still gets saved.
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda signum, _frame: child.send_signal(signum))

    out = sys.stdout.buffer
    while True:
        chunk = os.read(child.stdout.fileno(), CHUNK_BYTES)
        if not chunk:
            break
        out.write(chunk)
        out.flush()
        log.write(chunk)
        log.flush()
    code = child.wait()
    log.close()

    sync.stop.set()
    timer.join(timeout=HTTP_TIMEOUT_SECONDS)
    sync.finish()
    os.unlink(log.name)
    # A command killed by a signal exits like a shell would report it.
    return 128 - code if code < 0 else code


if __name__ == "__main__":
    sys.exit(main())
