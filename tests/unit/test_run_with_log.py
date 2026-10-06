"""Tests for argo/scrna/run_with_log.py, the wrapper every RNA-seq step runs through to keep
its log in Bloom's `run-logs` bucket. The wrapper runs as a real subprocess against a fake
Bloom (GoTrue sign-in and Storage upload) served on localhost."""

import importlib.util
import json
import subprocess
import sys
import threading
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
                    bloom.uploads.append(
                        {
                            "path": self.path.removeprefix(
                                "/storage/v1/object/run-logs/"
                            ),
                            "body": body.decode(),
                            "headers": {k.lower(): v for k, v in self.headers.items()},
                        }
                    )
                    return self._send(200, b'{"Key":"ok"}')
                return self._send(404, b"{}")

            def do_GET(self):
                if self.path.startswith("/elsewhere"):
                    return self._elsewhere()
                return self._send(404, b"{}")

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


# --------------------------------------------------------------------------- #
# The Cell Ranger template and the images run every step through the wrapper
# --------------------------------------------------------------------------- #

ARGO = Path(__file__).resolve().parents[2] / "argo" / "scrna"
# The run page's name for each template step (services/workflows/rnaseq_status.py).
STEP_NAMES = {
    "stage-reference": "stage-reference",
    "fetch-sra": "fetch-sra",
    "stage-sample": "stage",
    "qc": "qc",
    "count": "count",
    "preprocess": "preprocess",
    "cluster": "cluster",
    "build-h5ad": "build-h5ad",
    "cleanup": "cleanup",
}


def _templates():
    yaml = pytest.importorskip("yaml")
    doc = yaml.safe_load(
        (ARGO / "cellranger" / "cellranger-count-template.yaml").read_text()
    )
    return {t["name"]: t for t in doc["spec"]["templates"]}


@pytest.mark.parametrize("template, step", sorted(STEP_NAMES.items()))
def test_each_step_runs_through_the_wrapper_under_its_run_page_name(template, step):
    command = _templates()[template]["container"]["command"]
    assert command[:4] == [
        "run-with-log",
        "--path",
        f"scrna/{{{{workflow.name}}}}/{step}.log",
        "--",
    ]
    assert len(command) > 4, "the step's own command must follow --"


@pytest.mark.parametrize("template", sorted(STEP_NAMES))
def test_each_step_mounts_the_bloom_credential_read_only(template):
    mounts = _templates()[template]["container"]["volumeMounts"]
    assert {
        "name": "bloom-credentials",
        "mountPath": "/etc/bloom",
        "readOnly": True,
    } in mounts


def test_the_names_match_the_run_pages_steps():
    status = (
        Path(__file__).resolve().parents[2]
        / "services"
        / "workflows"
        / "rnaseq_status.py"
    ).read_text()
    for template, step in STEP_NAMES.items():
        assert f'"{template}": "{step}"' in status


def test_the_smoke_test_step_is_left_alone():
    assert _templates()["testrun"]["container"]["command"] == ["bash", "-c"]


def test_the_hand_submitted_workflow_defines_the_volume():
    yaml = pytest.importorskip("yaml")
    doc = yaml.safe_load(
        (ARGO / "cellranger" / "cellranger-count-workflow.yaml").read_text()
    )
    assert {"name": "bloom-credentials", "emptyDir": {}} in doc["spec"]["volumes"]


def test_the_services_volume_has_the_templates_name():
    source = (
        Path(__file__).resolve().parents[2]
        / "services"
        / "workflows"
        / "rnaseq_workflows.py"
    ).read_text()
    assert 'BLOOM_CREDENTIALS_VOLUME = "bloom-credentials"' in source


def test_both_step_images_install_the_wrapper():
    cellranger = (ARGO / "Dockerfile").read_text()
    analysis = (ARGO / "analysis" / "Dockerfile").read_text()
    assert "COPY run_with_log.py /usr/local/bin/run-with-log" in cellranger
    assert (
        "/usr/local/bin/run-with-log"
        in cellranger.split("RUN chmod +x", 1)[1].split("\n", 1)[0]
    )
    assert "COPY --from=scrna run_with_log.py /usr/local/bin/run-with-log" in analysis
    assert (
        "chmod +x /usr/local/bin/scrna-analysis /usr/local/bin/run-with-log" in analysis
    )


def test_the_wrapper_runs_as_a_script():
    assert WRAPPER.read_text().startswith("#!/usr/bin/env python3\n")
