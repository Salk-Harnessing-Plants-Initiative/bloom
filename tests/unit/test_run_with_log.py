"""Tests for argo/scrna/run_with_log.py, the wrapper every RNA-seq step runs through to keep
its log in Bloom's `run-logs` bucket. The wrapper runs as a real subprocess against a fake
Bloom (GoTrue sign-in and Storage upload) served on localhost."""

import importlib.util
import json
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

WRAPPER = Path(__file__).resolve().parents[2] / "argo" / "scrna" / "run_with_log.py"
TOKEN = "fake-access-token-1234567890"
PASSWORD = "fake-password-abcdef"
LOG_PATH = "scrna/scrna-cellranger-staging-5-8b939a02/count.log"


class FakeBloom:
    """Records sign-ins and uploads; can be told to fail uploads or reject a token once."""

    def __init__(self):
        self.sign_ins = 0
        self.uploads: list[dict] = []
        self.upload_status = 200
        self.reject_next_upload = False
        self.redirect_uploads = False
        self.redirected: list[dict] = []
        # What Storage holds per path, as earlier attempts uploaded it.
        self.stored: dict[str, str] = {}
        self.download_status = 200
        self.missing_as_400 = False
        bloom = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
                if self.path.startswith("/auth/v1/token"):
                    bloom.sign_ins += 1
                    reply = {
                        "access_token": f"{TOKEN}-{bloom.sign_ins}",
                        "expires_in": 3600,
                    }
                    return self._send(200, json.dumps(reply).encode())
                if self.path.startswith("/elsewhere"):
                    return self._elsewhere()
                if self.path.startswith("/storage/v1/object/run-logs/"):
                    if bloom.redirect_uploads:
                        self.send_response(302)
                        self.send_header("Location", f"{bloom.url}/elsewhere")
                        self.send_header("Content-Length", "0")
                        self.end_headers()
                        return None
                    if bloom.reject_next_upload:
                        bloom.reject_next_upload = False
                        return self._send(403, b'{"error":"jwt expired"}')
                    if bloom.upload_status != 200:
                        return self._send(bloom.upload_status, b'{"error":"boom"}')
                    path = self.path.removeprefix("/storage/v1/object/run-logs/")
                    bloom.stored[path] = body.decode()
                    bloom.uploads.append(
                        {
                            "path": path,
                            "body": body.decode(),
                            "headers": {k.lower(): v for k, v in self.headers.items()},
                        }
                    )
                    return self._send(200, b'{"Key":"ok"}')
                return self._send(404, b"{}")

            def do_GET(self):
                if self.path.startswith("/elsewhere"):
                    return self._elsewhere()
                prefix = "/storage/v1/object/authenticated/run-logs/"
                if self.path.startswith(prefix):
                    return self._download(self.path.removeprefix(prefix))
                return self._send(404, b"{}")

            def _download(self, path):
                if bloom.download_status != 200:
                    return self._send(bloom.download_status, b'{"error":"boom"}')
                if path in bloom.stored:
                    return self._send(200, bloom.stored[path].encode())
                if bloom.missing_as_400:
                    return self._send(400, b'{"statusCode":"404","error":"not_found"}')
                return self._send(404, b'{"error":"not_found"}')

            def _elsewhere(self):
                bloom.redirected.append({k.lower(): v for k, v in self.headers.items()})
                return self._send(200, b"{}")

            def _send(self, status, body):
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()


@pytest.fixture
def bloom():
    fake = FakeBloom()
    yield fake
    fake.close()


@pytest.fixture
def credentials(tmp_path, bloom):
    path = tmp_path / "credentials.txt"
    path.write_text(
        f"BLOOM_API_URL={bloom.url}\n"
        "BLOOM_ANON_KEY=fake-anon-key\n"
        "BLOOM_EMAIL=pipeline@bloom.test\n"
        f'BLOOM_PASSWORD="{PASSWORD}"\n'
    )
    return path


def _run(command, credentials_path, interval="0.2", timeout=60):
    env = {
        "BLOOM_CREDENTIALS": str(credentials_path),
        "RUN_LOG_INTERVAL": interval,
        "PATH": "/usr/bin:/bin",
    }
    return subprocess.run(
        [sys.executable, str(WRAPPER), "--path", LOG_PATH, "--", *command],
        capture_output=True,
        text=True,
        env=env,
        timeout=timeout,
    )


def _py(code):
    return [sys.executable, "-c", code]


def test_the_whole_log_is_uploaded_when_the_command_ends(bloom, credentials):
    result = _run(_py("print('aligning'); print('counting')"), credentials)
    assert result.returncode == 0
    assert result.stdout == "aligning\ncounting\n", (
        "output must still reach the pod's log"
    )
    last = bloom.uploads[-1]
    assert last["path"] == LOG_PATH
    assert last["body"] == "aligning\ncounting\n"
    assert last["headers"]["content-type"] == "text/plain"
    assert last["headers"]["x-upsert"] == "true"
    assert last["headers"]["authorization"].startswith(f"Bearer {TOKEN}")


def test_a_running_step_is_uploaded_as_it_grows(bloom, credentials):
    result = _run(
        _py(
            "import time; print('first', flush=True); time.sleep(1.5); print('second')"
        ),
        credentials,
    )
    assert result.returncode == 0
    bodies = [u["body"] for u in bloom.uploads]
    assert "first\n" in bodies, "nothing was uploaded while the step was running"
    assert bodies[-1] == "first\nsecond\n"


def test_an_unchanged_log_is_not_uploaded_again(bloom, credentials):
    _run(_py("import time; print('once', flush=True); time.sleep(1.2)"), credentials)
    assert [u["body"] for u in bloom.uploads] == ["once\n"]


def test_the_commands_exit_code_is_kept(bloom, credentials):
    result = _run(_py("import sys; print('failing'); sys.exit(7)"), credentials)
    assert result.returncode == 7
    assert bloom.uploads[-1]["body"] == "failing\n", (
        "a failed step's log must be uploaded too"
    )


def test_stderr_is_kept_with_stdout(bloom, credentials):
    _run(
        _py("import sys; print('out', flush=True); print('err', file=sys.stderr)"),
        credentials,
    )
    assert bloom.uploads[-1]["body"] == "out\nerr\n"


def test_a_failed_upload_never_fails_the_step(bloom, credentials):
    bloom.upload_status = 500
    result = _run(_py("print('fine')"), credentials, timeout=90)
    assert result.returncode == 0
    assert "couldn't upload the log (HTTP 500)" in result.stderr
    assert "the end of the log wasn't uploaded" in result.stderr


def test_a_rejected_token_signs_in_again(bloom, credentials):
    bloom.reject_next_upload = True
    result = _run(_py("print('fine')"), credentials)
    assert result.returncode == 0
    assert bloom.sign_ins == 2
    assert bloom.uploads[-1]["body"] == "fine\n"


def test_without_credentials_the_step_runs_and_nothing_is_uploaded(tmp_path, bloom):
    result = _run(
        _py("import sys; print('fine'); sys.exit(3)"), tmp_path / "missing.txt"
    )
    assert result.returncode == 3
    assert result.stdout == "fine\n"
    assert "no Bloom credentials" in result.stderr
    assert bloom.sign_ins == 0 and bloom.uploads == []


def test_a_bloom_url_that_isnt_https_is_never_signed_in_to(tmp_path, bloom):
    path = tmp_path / "credentials.txt"
    path.write_text(
        "BLOOM_API_URL=http://bloom.example.org/api\n"
        "BLOOM_ANON_KEY=fake-anon-key\n"
        "BLOOM_EMAIL=pipeline@bloom.test\n"
        f"BLOOM_PASSWORD={PASSWORD}\n"
    )
    result = _run(_py("import sys; print('fine'); sys.exit(3)"), path)
    assert result.returncode == 3
    assert "BLOOM_API_URL isn't https" in result.stderr


@pytest.mark.parametrize(
    "url, safe",
    [
        ("https://bloom.salk.edu/api", True),
        ("http://bloom.salk.edu/api", False),
        ("http://127.0.0.1:8000", True),
        ("http://localhost:8000", True),
        ("ftp://bloom.salk.edu", False),
        ("https://", False),
    ],
)
def test_only_https_or_this_machine_is_signed_in_to(url, safe):
    assert _module().is_safe_api_url(url) is safe


def test_a_redirect_is_not_followed_with_the_token(bloom, credentials):
    bloom.redirect_uploads = True
    result = _run(_py("print('fine')"), credentials, timeout=90)
    assert result.returncode == 0
    assert bloom.redirected == [], "the upload followed a redirect"
    assert "HTTP 302" in result.stderr


def test_a_command_that_cant_start_exits_127_and_says_why(bloom, credentials):
    result = _run(["/no/such/command"], credentials)
    assert result.returncode == 127
    assert "can't run /no/such/command" in bloom.uploads[-1]["body"]


def test_the_token_and_password_are_never_printed(bloom, credentials):
    bloom.reject_next_upload = True
    result = _run(_py("print('fine')"), credentials)
    for text in (result.stdout, result.stderr, *(u["body"] for u in bloom.uploads)):
        assert TOKEN not in text and PASSWORD not in text


def _module():
    spec = importlib.util.spec_from_file_location("run_with_log", WRAPPER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_a_long_log_keeps_its_end(tmp_path, monkeypatch):
    module = _module()
    monkeypatch.setattr(module, "MAX_UPLOAD_BYTES", 10)
    path = tmp_path / "log"
    path.write_bytes(b"0123456789ABCDEFGHIJ")
    data = module._read_tail(str(path))
    assert data.endswith(b"ABCDEFGHIJ")
    assert data.startswith(b"[run-with-log: the first 10 bytes were left out]\n")


@pytest.mark.parametrize(
    "text, complete",
    [
        ("BLOOM_API_URL=u\nBLOOM_ANON_KEY=k\nBLOOM_EMAIL=e\nBLOOM_PASSWORD=p\n", True),
        (
            "export BLOOM_API_URL='u'\nBLOOM_ANON_KEY=\"k\"\n# note\nBLOOM_EMAIL=e\nBLOOM_PASSWORD=p",
            True,
        ),
        ("BLOOM_API_URL=u\nBLOOM_ANON_KEY=k\nBLOOM_EMAIL=e\n", False),
        ("BLOOM_API_URL=u\nBLOOM_ANON_KEY=k\nBLOOM_EMAIL=e\nBLOOM_PASSWORD=\n", False),
    ],
    ids=["plain", "quoted-and-exported", "missing-key", "empty-value"],
)
def test_the_credentials_file_is_read_as_bloomctl_writes_it(tmp_path, text, complete):
    path = tmp_path / "credentials.txt"
    path.write_text(text)
    values = _module().read_credentials(str(path))
    if complete:
        assert values == {
            "BLOOM_API_URL": "u",
            "BLOOM_ANON_KEY": "k",
            "BLOOM_EMAIL": "e",
            "BLOOM_PASSWORD": "p",
        }
    else:
        assert values is None


def test_a_process_left_holding_the_output_doesnt_keep_the_step_running(
    bloom, credentials
):
    # The command exits with 4 but leaves a process that holds its output open for 30 s.
    command = _py(
        "import subprocess, sys; subprocess.Popen(['sleep', '30']); "
        "print('done', flush=True); sys.exit(4)"
    )
    started = time.monotonic()
    result = _run(command, credentials)
    assert result.returncode == 4
    assert time.monotonic() - started < 20, (
        "the wrapper waited for the leftover process"
    )
    assert bloom.uploads[-1]["body"] == "done\n"


# --------------------------------------------------------------------------- #
# A retried step adds to the earlier attempts' log
# --------------------------------------------------------------------------- #


def test_a_retried_step_adds_to_the_earlier_attempts_log(bloom, credentials):
    first = _run(_py("import sys; print('out of memory'); sys.exit(137)"), credentials)
    assert first.returncode == 137
    second = _run(_py("print('counted')"), credentials)
    assert second.returncode == 0
    assert second.stdout == "counted\n", (
        "the earlier attempt must not reach the pod's log"
    )
    body = bloom.uploads[-1]["body"]
    assert body.startswith("out of memory\n--- run-with-log: retried at ")
    assert body.endswith("; earlier attempts above ---\ncounted\n")


@pytest.mark.parametrize(
    "missing_as_400", [False, True], ids=["404", "older-storage-400"]
)
def test_a_first_attempt_starts_a_new_log(bloom, credentials, missing_as_400):
    bloom.missing_as_400 = missing_as_400
    result = _run(_py("print('fine')"), credentials)
    assert bloom.uploads[-1]["body"] == "fine\n"
    assert "earlier attempts" not in result.stderr


def test_an_unreadable_earlier_log_starts_a_new_one_and_says_so(bloom, credentials):
    bloom.stored[LOG_PATH] = "lost\n"
    bloom.download_status = 500
    result = _run(_py("print('fine')"), credentials)
    assert result.returncode == 0
    assert "couldn't read earlier attempts' log (HTTP 500)" in result.stderr
    assert bloom.uploads[-1]["body"] == "fine\n"
