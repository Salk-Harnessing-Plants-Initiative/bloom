#!/usr/bin/env python3
"""run-with-log: run a pipeline step and keep its log in Bloom's storage.

Usage: run-with-log --path scrna/<workflow name>/<step>.log -- <command> [args...]

Runs the command, passes everything it prints through to the pod's own output, saves a copy,
and uploads that copy to the `run-logs` bucket every RUN_LOG_INTERVAL seconds (30) while it
grows, and once more when the command ends. A retried step adds to the earlier attempts' log,
under a marker line. Exits with the command's exit code. Uploads never fail the step: a failed
upload is noted on stderr and tried again at the next interval.

Signs in to Bloom with bloomctl's credentials file (BLOOM_API_URL, BLOOM_ANON_KEY,
BLOOM_EMAIL, BLOOM_PASSWORD) at BLOOM_CREDENTIALS (/etc/bloom/credentials.txt), over https
only, and follows no redirect. Without it, the command runs and nothing is uploaded. The token
and password are never printed.
"""

from __future__ import annotations

import argparse
import json
import os
import select
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
# How often the read loop checks whether the command has exited.
POLL_SECONDS = 0.5
# After the command exits, output still on its way is read for this long. A process it left
# behind can hold the output open forever, so the wrapper doesn't wait for that.
DRAIN_SECONDS = 5.0
# The last upload, when the command has ended, is tried this many times.
FINAL_ATTEMPTS = 3
FINAL_RETRY_SECONDS = 2.0
# Plain http only to this machine (tests); anywhere else the password would cross in clear.
LOCAL_HOSTS = ("localhost", "127.0.0.1", "::1")


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


def is_safe_api_url(url: str) -> bool:
    """True for an https URL, or http to this machine."""
    parts = urllib.parse.urlsplit(url)
    if parts.scheme == "https":
        return bool(parts.hostname)
    return parts.scheme == "http" and parts.hostname in LOCAL_HOSTS


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Refuses every redirect, which would carry the token to wherever it points."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise urllib.error.HTTPError(
            req.full_url, code, "redirect refused", headers, fp
        )


_OPENER = urllib.request.build_opener(_NoRedirect)


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

    def _request(
        self,
        url: str,
        body: bytes | None,
        headers: dict[str, str],
        method: str = "POST",
    ) -> bytes:
        request = urllib.request.Request(url, data=body, headers=headers, method=method)
        with _OPENER.open(request, timeout=HTTP_TIMEOUT_SECONDS) as response:
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

    def _signed_in(
        self, method: str, url: str, body: bytes | None, headers: dict
    ) -> bytes:
        """A request with the token, signing in first when needed and once more if refused."""
        if (
            self.token is None
            or time.time() > self.token_expires - TOKEN_MARGIN_SECONDS
        ):
            self._sign_in()
        try:
            return self._request(url, body, self._auth(headers), method)
        except urllib.error.HTTPError as exc:
            if exc.code not in (401, 403):
                raise
            # The token may have been revoked or expired early: sign in once more.
            self._sign_in()
            return self._request(url, body, self._auth(headers), method)

    def _auth(self, headers: dict[str, str]) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.token}",
            "apikey": self.anon_key,
            **headers,
        }

    def _object_url(self, prefix: str = "") -> str:
        path = urllib.parse.quote(self.object_path)
        return f"{self.api}/storage/v1/object/{prefix}{BUCKET}/{path}"

    def upload(self, data: bytes) -> None:
        """Upload `data` in place of the stored log. Raises on failure."""
        self._signed_in(
            "POST",
            self._object_url(),
            data,
            {"Content-Type": "text/plain", "x-upsert": "true"},
        )

    def download(self) -> bytes | None:
        """The stored log, or None if there is none yet. Raises on any other failure."""
        try:
            return self._signed_in("GET", self._object_url("authenticated/"), None, {})
        except urllib.error.HTTPError as exc:
            if exc.code == 404 or (exc.code == 400 and _says_not_found(exc)):
                return None
            raise


def _says_not_found(exc: urllib.error.HTTPError) -> bool:
    """Older Storage answers a missing object with 400 and "not_found" in the body."""
    try:
        return b"not_found" in exc.read().lower().replace(b" ", b"_")
    except OSError:
        return False


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


def _copy_output(child: subprocess.Popen, out, log) -> None:
    """Copy the command's output to the pod's output and the log until the command has exited
    and its output is read, or DRAIN_SECONDS after it exited."""
    fd = child.stdout.fileno()
    drain_until = None
    while True:
        now = time.monotonic()
        if drain_until is None and child.poll() is not None:
            drain_until = now + DRAIN_SECONDS
        if drain_until is not None and now >= drain_until:
            return
        wait = POLL_SECONDS if drain_until is None else drain_until - now
        ready, _, _ = select.select([fd], [], [], wait)
        if not ready:
            continue
        chunk = os.read(fd, CHUNK_BYTES)
        if not chunk:
            return
        out.write(chunk)
        out.flush()
        log.write(chunk)
        log.flush()


def _keep_earlier_attempts(uploader: Uploader, log) -> None:
    """Start the log with what earlier attempts of this step uploaded, so a retry adds to it."""
    try:
        earlier = uploader.download()
    except Exception as exc:  # noqa: BLE001 - a missing history must never fail the step
        _note(
            f"couldn't read earlier attempts' log ({_describe(exc)}); starting a new one"
        )
        return
    if not earlier:
        return
    when = time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime())
    log.write(earlier.rstrip(b"\n") + b"\n")
    log.write(
        f"--- run-with-log: retried at {when}; earlier attempts above ---\n".encode()
    )
    log.flush()


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
    uploader = None
    if credentials is None:
        _note("no Bloom credentials, so the log isn't uploaded")
    elif not is_safe_api_url(credentials["BLOOM_API_URL"]):
        _note("BLOOM_API_URL isn't https, so the log isn't uploaded")
    else:
        uploader = Uploader(credentials, args.path)
        _note(f"the log is kept at {BUCKET}/{args.path}")

    interval = float(os.environ.get("RUN_LOG_INTERVAL", DEFAULT_INTERVAL_SECONDS))
    log = tempfile.NamedTemporaryFile(prefix="run-log-", suffix=".log", delete=False)
    if uploader is not None:
        _keep_earlier_attempts(uploader, log)
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

    _copy_output(child, sys.stdout.buffer, log)
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
