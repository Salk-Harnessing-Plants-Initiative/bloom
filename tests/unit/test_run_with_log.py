"""Tests for argo/scrna/run_with_log.py, the wrapper every RNA-seq step runs through to keep
its log in Bloom's `run-logs` bucket: the step's output, exit code and signals, and what is
uploaded. The wrapper runs as a real subprocess against a fake Bloom on localhost."""

import signal
import subprocess
import sys
import time

import pytest

from tests.unit import _fake_bloom as fb


@pytest.fixture
def bloom():
    fake = fb.FakeBloom()
    yield fake
    fake.close()


@pytest.fixture
def credentials(tmp_path, bloom):
    return fb.write_credentials(tmp_path, bloom.url)


def test_the_whole_log_is_uploaded_when_the_command_ends(bloom, credentials):
    result = fb.run(fb.py("print('aligning'); print('counting')"), credentials)
    assert result.returncode == 0
    assert result.stdout == "aligning\ncounting\n", (
        "output must still reach the pod's log"
    )
    last = bloom.uploads[-1]
    assert last["path"] == fb.LOG_PATH
    assert last["body"] == "aligning\ncounting\n"
    assert last["headers"]["content-type"] == "text/plain"
    assert last["headers"]["x-upsert"] == "true"
    assert last["headers"]["authorization"].startswith(f"Bearer {fb.TOKEN}")


def test_a_running_step_is_uploaded_as_it_grows(bloom, credentials):
    result = fb.run(
        fb.py(
            "import time; print('first', flush=True); time.sleep(1.5); print('second')"
        ),
        credentials,
    )
    assert result.returncode == 0
    bodies = [u["body"] for u in bloom.uploads]
    assert "first\n" in bodies, "nothing was uploaded while the step was running"
    assert bodies[-1] == "first\nsecond\n"


def test_an_unchanged_log_is_not_uploaded_again(bloom, credentials):
    fb.run(
        fb.py("import time; print('once', flush=True); time.sleep(1.2)"), credentials
    )
    # A tick before the command's first line uploads an empty log; that's fine.
    assert [u["body"] for u in bloom.uploads if u["body"]] == ["once\n"]


def test_the_commands_exit_code_is_kept(bloom, credentials):
    result = fb.run(fb.py("import sys; print('failing'); sys.exit(7)"), credentials)
    assert result.returncode == 7
    assert bloom.uploads[-1]["body"] == "failing\n", (
        "a failed step's log must be uploaded too"
    )


def test_stderr_is_kept_with_stdout(bloom, credentials):
    fb.run(
        fb.py("import sys; print('out', flush=True); print('err', file=sys.stderr)"),
        credentials,
    )
    assert bloom.uploads[-1]["body"] == "out\nerr\n"


def test_a_command_killed_by_a_signal_exits_as_a_shell_reports_it(bloom, credentials):
    result = fb.run(
        fb.py(
            "import os, signal; print('dying', flush=True); os.kill(os.getpid(), signal.SIGKILL)"
        ),
        credentials,
    )
    assert result.returncode == 128 + signal.SIGKILL
    assert bloom.uploads[-1]["body"] == "dying\n"


def test_a_stop_reaches_the_command_and_its_last_words_are_kept(bloom, credentials):
    # Argo stops a step with SIGTERM; the wrapper passes it on and uploads what follows.
    child = fb.py(
        "import signal, sys, time\n"
        "def stop(*_):\n"
        "    print('cleaning up', flush=True)\n"
        "    sys.exit(143)\n"
        "signal.signal(signal.SIGTERM, stop)\n"
        "print('ready', flush=True)\n"
        "time.sleep(30)\n"
    )
    env = {
        "BLOOM_CREDENTIALS": str(credentials),
        "RUN_LOG_INTERVAL": "60",
        "PATH": "/usr/bin:/bin",
    }
    wrapper = subprocess.Popen(
        [sys.executable, str(fb.WRAPPER), "--path", fb.LOG_PATH, "--", *child],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
    )
    assert wrapper.stdout.readline() == b"ready\n"
    wrapper.send_signal(signal.SIGTERM)
    assert wrapper.wait(timeout=30) == 143
    wrapper.stdout.close()
    wrapper.stderr.close()
    assert bloom.uploads[-1]["body"] == "ready\ncleaning up\n"


def test_a_command_that_cant_start_exits_127_and_says_why(bloom, credentials):
    result = fb.run(["/no/such/command"], credentials)
    assert result.returncode == 127
    assert "can't run /no/such/command" in bloom.uploads[-1]["body"]


def test_a_long_log_keeps_its_end(tmp_path, monkeypatch):
    module = fb.wrapper_module()
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
    values = fb.wrapper_module().read_credentials(str(path))
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
    command = fb.py(
        "import subprocess, sys; subprocess.Popen(['sleep', '30']); "
        "print('done', flush=True); sys.exit(4)"
    )
    started = time.monotonic()
    result = fb.run(command, credentials)
    assert result.returncode == 4
    assert time.monotonic() - started < 20, (
        "the wrapper waited for the leftover process"
    )
    assert bloom.uploads[-1]["body"] == "done\n"
