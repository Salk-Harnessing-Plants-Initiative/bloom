"""A fake Bloom (GoTrue sign-in, Storage upload and download) on localhost, and running
argo/scrna/run_with_log.py against it, for the run-with-log tests."""

import importlib.util
import json
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


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
        self.reject_status = 403
        self.fail_next_uploads = 0
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
                        return self._send(
                            bloom.reject_status, b'{"error":"jwt expired"}'
                        )
                    if bloom.fail_next_uploads:
                        bloom.fail_next_uploads -= 1
                        return self._send(503, b'{"error":"busy"}')
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


def run(command, credentials_path, interval="0.2", timeout=60):
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


def py(code):
    return [sys.executable, "-c", code]


def write_credentials(directory, url):
    """bloomctl's credentials file pointing at `url`."""
    path = directory / "credentials.txt"
    path.write_text(
        f"BLOOM_API_URL={url}\n"
        "BLOOM_ANON_KEY=fake-anon-key\n"
        "BLOOM_EMAIL=pipeline@bloom.test\n"
        f'BLOOM_PASSWORD="{PASSWORD}"\n'
    )
    return path


def wrapper_module():
    spec = importlib.util.spec_from_file_location("run_with_log", WRAPPER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module
