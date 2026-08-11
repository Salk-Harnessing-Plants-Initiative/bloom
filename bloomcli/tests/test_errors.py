"""The last-resort handler at the entry point.

Commands are expected to raise `click.ClickException` with a message that says what to do.
This covers what happens when something raises that nobody planned for — which used to reach
the user as a stack trace (#629).

Worth knowing: the rest of the suite invokes `cli` through `CliRunner`, which bypasses
`main()` entirely. Nothing else here exercises the real entry point.
"""

from __future__ import annotations

import errno
import stat
from pathlib import Path

import click
import pytest

from bloomctl import errors

# --- turning an exception into a sentence ------------------------------------


def test_a_write_error_reads_as_the_reason_and_the_file():
    exc = OSError(errno.ENOSPC, "No space left on device", "/out/download_log.txt")

    assert errors.explain(exc) == "No space left on device: /out/download_log.txt"


def test_a_write_error_without_a_filename_is_just_the_reason():
    assert errors.explain(OSError(errno.EACCES, "Permission denied")) == "Permission denied"


def test_an_api_error_keeps_the_server_wording():
    class _ApiError(Exception):
        message = "permission denied for table cyl_scans"

    assert errors.explain(_ApiError()) == "permission denied for table cyl_scans"


def test_a_dropped_connection_says_so_even_though_it_carries_no_message():
    httpx = pytest.importorskip("httpx")

    explained = errors.explain(httpx.ReadTimeout(""))

    assert "could not reach Bloom" in explained
    assert "ReadTimeout" in explained


def test_an_unrecognised_error_still_gives_one_line_not_a_stack():
    assert errors.explain(ValueError("something odd")) == "something odd"


def test_an_error_with_no_message_at_all_falls_back_to_its_type():
    assert errors.explain(ValueError()) == "ValueError"


# --- recording the traceback -------------------------------------------------


def _raise(exc: BaseException) -> BaseException:
    """Give ``exc`` a real traceback, the way it would have one in flight."""
    try:
        raise exc
    except BaseException as caught:  # noqa: B036 - re-raised immediately by the caller
        return caught


def test_the_traceback_goes_to_the_log_with_the_command_that_caused_it(tmp_path):
    log = tmp_path / "errors.log"

    errors.record(_raise(ValueError("boom")), ["bloomctl", "cyl", "download"], path=log)

    text = log.read_text()
    assert "ValueError: boom" in text
    assert "Traceback (most recent call last)" in text
    assert "bloomctl cyl download" in text


def test_a_second_failure_is_appended_rather_than_replacing_the_first(tmp_path):
    log = tmp_path / "errors.log"

    errors.record(_raise(ValueError("first")), ["bloomctl", "a"], path=log)
    errors.record(_raise(ValueError("second")), ["bloomctl", "b"], path=log)

    text = log.read_text()
    assert "first" in text and "second" in text


def test_recording_never_raises_when_the_log_cannot_be_written(tmp_path, monkeypatch):
    """This runs while already handling a failure; a full disk is one of the failures it
    has to survive. Raising here would replace one traceback with a worse one."""

    def _no_space(*args, **kwargs):
        raise OSError(errno.ENOSPC, "No space left on device")

    monkeypatch.setattr(Path, "open", _no_space)
    monkeypatch.setattr(Path, "mkdir", _no_space)
    log = tmp_path / "errors.log"

    returned = errors.record(_raise(ValueError("boom")), ["bloomctl"], path=log)

    assert returned is None, "nothing was written, so there is nowhere to send the user"
    assert not log.exists()


def test_recording_survives_a_command_line_that_cannot_be_encoded(tmp_path):
    """A path the shell passed as undecodable bytes reaches argv as surrogates.

    Encoding those raises ValueError, not OSError, so a guard that only caught OSError let it
    out of the handler — replacing the traceback with a traceback, which is the one thing this
    must never do.
    """
    log = tmp_path / "errors.log"
    argv = ["bloomctl", "cyl", "download", "/mnt/\udcff\udcfe"]

    returned = errors.record(_raise(ValueError("boom")), argv, path=log)

    assert returned == log
    assert "boom" in log.read_text(), "the traceback is kept; only the bad bytes are replaced"


def test_nothing_is_reported_when_the_log_was_created_but_never_written(tmp_path, monkeypatch):
    """`touch` succeeding is not the same as the traceback landing.

    Reporting on the file existing would send someone to an empty log.
    """
    log = tmp_path / "errors.log"

    def _fail(*args, **kwargs):
        raise OSError(errno.EACCES, "Permission denied")

    monkeypatch.setattr(Path, "chmod", _fail)  # after touch has created the file

    returned = errors.record(_raise(ValueError("boom")), ["bloomctl"], path=log)

    assert returned is None
    assert log.exists() and log.read_text() == "", "the empty file is exactly the trap"


def test_the_log_is_capped_so_it_cannot_grow_forever(tmp_path):
    log = tmp_path / "errors.log"
    log.write_bytes(b"x" * (errors.MAX_LOG_BYTES + 1))

    errors.record(_raise(ValueError("boom")), ["bloomctl"], path=log)

    assert log.stat().st_size < errors.MAX_LOG_BYTES
    assert "boom" in log.read_text()
    assert "earlier entries dropped" in log.read_text()


def test_a_failed_rotation_keeps_the_log_it_was_called_to_preserve(tmp_path, monkeypatch):
    """Rotation rewrites the whole file, and doing that in place would empty it first.

    It only runs on a machine that has been failing often enough to fill the log, which is
    when those tracebacks are worth the most.
    """
    log = tmp_path / "errors.log"
    log.write_bytes(b"y" * (errors.MAX_LOG_BYTES + 1))

    real = Path.write_bytes

    def _no_space(self, data):
        real(self, b"")  # the temp file is created, then the disk gives out
        raise OSError(errno.ENOSPC, "No space left on device")

    monkeypatch.setattr(Path, "write_bytes", _no_space)
    errors._trim(log)
    monkeypatch.undo()

    assert log.stat().st_size == errors.MAX_LOG_BYTES + 1, "the log it could not rotate is intact"
    assert not list(tmp_path.glob(".errors.log.*.tmp")), "no temp file left behind"


def test_the_log_sits_beside_the_credentials(tmp_path):
    assert errors.error_log_path(tmp_path) == tmp_path / "errors.log"


# --- the entry point ---------------------------------------------------------


@pytest.fixture
def _log_in_tmp(tmp_path, monkeypatch):
    """Keep the tests off the real ~/.bloom/errors.log."""
    log = tmp_path / "errors.log"
    monkeypatch.setattr(errors, "error_log_path", lambda *a, **k: log)
    return log


def test_an_unhandled_failure_becomes_a_message_and_a_log_entry(_log_in_tmp, monkeypatch, capsys):
    @click.command()
    def boom():
        raise RuntimeError("nobody planned for this")

    monkeypatch.setattr("bloomctl.cli.cli", boom)

    code = errors.main(args=[])

    err = capsys.readouterr().err
    assert code == 1
    assert "Error: nobody planned for this" in err
    assert str(_log_in_tmp) in err
    assert "Traceback" not in err, "the stack belongs in the log, not on screen"
    assert "RuntimeError: nobody planned for this" in _log_in_tmp.read_text()


def test_a_clean_message_from_a_command_is_left_alone(_log_in_tmp, monkeypatch, capsys):
    """The specific message is the useful one; the net must not reword or intercept it."""

    @click.command()
    def known():
        raise click.ClickException("Scan 42 not found.")

    monkeypatch.setattr("bloomctl.cli.cli", known)

    with pytest.raises(SystemExit) as exit_info:
        errors.main(args=[])

    assert exit_info.value.code == 1
    assert "Error: Scan 42 not found." in capsys.readouterr().err
    assert not _log_in_tmp.exists(), "an expected failure is not worth a traceback"


def test_a_successful_command_is_untouched(_log_in_tmp, monkeypatch, capsys):
    @click.command()
    def fine():
        click.echo("done")

    monkeypatch.setattr("bloomctl.cli.cli", fine)

    with pytest.raises(SystemExit) as exit_info:
        errors.main(args=[])

    assert exit_info.value.code == 0
    assert capsys.readouterr().out.strip() == "done"
    assert not _log_in_tmp.exists()


def test_ctrl_c_still_aborts_rather_than_being_caught(_log_in_tmp, monkeypatch, capsys):
    @click.command()
    def interrupted():
        raise KeyboardInterrupt

    monkeypatch.setattr("bloomctl.cli.cli", interrupted)

    with pytest.raises(SystemExit):
        errors.main(args=[])

    assert "Aborted" in capsys.readouterr().err
    assert not _log_in_tmp.exists(), "an interrupt is the user's decision, not a failure"


def test_a_failure_is_still_reported_when_the_log_cannot_be_written(
    _log_in_tmp, monkeypatch, capsys
):
    @click.command()
    def boom():
        raise RuntimeError("nobody planned for this")

    monkeypatch.setattr("bloomctl.cli.cli", boom)
    monkeypatch.setattr(errors, "record", lambda *a, **k: None)  # nothing could be written

    code = errors.main(args=[])

    err = capsys.readouterr().err
    assert code == 1
    assert "Error: nobody planned for this" in err
    assert "Details written to" not in err, "do not point at a log that isn't there"


# --- the log must be safe to send us ------------------------------------------


def test_a_password_never_reaches_the_log(tmp_path):
    """The CLI tells people where this file is so they can send it on. A password in it
    would travel with it."""
    log = tmp_path / "errors.log"
    argv = ["bloomctl", "login", "--email", "me@salk.edu", "--password", "SuperSecret123!"]

    errors.record(_raise(ValueError("boom")), argv, path=log)

    text = log.read_text()
    assert "SuperSecret123!" not in text
    assert "--password ***" in text
    assert "me@salk.edu" in text, "the rest of the command is still useful"


def test_the_equals_form_of_a_password_is_redacted_too(tmp_path):
    log = tmp_path / "errors.log"

    errors.record(_raise(ValueError("boom")), ["bloomctl", "login", "--password=Secret!"], path=log)

    text = log.read_text()
    assert "Secret!" not in text
    assert "--password=***" in text


def test_redact_leaves_an_ordinary_command_alone():
    argv = ["bloomctl", "cyl", "download", "./out", "--experiment-id", "42", "--workers", "16"]

    assert errors.redact(argv) == argv


def test_an_anon_key_never_reaches_the_log(tmp_path):
    """The key this flag is for is public, but it is the one a service-role key is pasted
    into by mistake, and the value is no use in a traceback either way."""
    log = tmp_path / "errors.log"
    argv = ["bloomctl", "login", "--api-url", "https://x/api", "--anon-key", "eyJhbGciOi.Jsecret"]

    errors.record(_raise(ValueError("boom")), argv, path=log)

    text = log.read_text()
    assert "eyJhbGciOi.Jsecret" not in text
    assert "--anon-key ***" in text
    assert "https://x/api" in text, "the rest of the command is still useful"


def test_the_equals_form_of_an_anon_key_is_redacted_too():
    assert errors.redact(["bloomctl", "login", "--anon-key=eyJsecret"]) == [
        "bloomctl",
        "login",
        "--anon-key=***",
    ]


def test_the_profile_flag_is_not_mistaken_for_a_secret():
    """`-p` is `--profile`; the redaction only matches long names, and this locks that in."""
    argv = ["bloomctl", "-p", "staging", "cyl", "download", "./out"]

    assert errors.redact(argv) == argv


def test_the_log_is_not_readable_by_others(tmp_path):
    """It sits beside the credentials, which the same package deliberately writes 0600."""
    log = tmp_path / "bloom" / "errors.log"

    errors.record(_raise(ValueError("boom")), ["bloomctl"], path=log)

    assert stat.S_IMODE(log.stat().st_mode) == 0o600
    assert stat.S_IMODE(log.parent.stat().st_mode) == 0o700, (
        "this may be what creates ~/.bloom, and a later mkdir cannot tighten it"
    )


def test_a_log_left_readable_by_an_earlier_version_is_tightened(tmp_path):
    log = tmp_path / "errors.log"
    log.write_text("from a version that wrote this 0644\n")
    log.chmod(0o644)

    errors.record(_raise(ValueError("boom")), ["bloomctl"], path=log)

    assert stat.S_IMODE(log.stat().st_mode) == 0o600
