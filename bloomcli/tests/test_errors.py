"""The last-resort handler at the entry point.

Commands are expected to raise `click.ClickException` with a message that says what to do.
This covers what happens when something raises that nobody planned for — which used to reach
the user as a stack trace (#629).

Worth knowing: the rest of the suite invokes `cli` through `CliRunner`, which bypasses
`main()` entirely. Nothing else here exercises the real entry point.
"""

from __future__ import annotations

import errno
import os
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

    Encoding those would raise; `errors="replace"` spends a few question marks instead, so
    the traceback the log exists for still lands.
    """
    log = tmp_path / "errors.log"
    argv = ["bloomctl", "cyl", "download", "/mnt/\udcff\udcfe"]

    returned = errors.record(_raise(ValueError("boom")), argv, path=log)

    assert returned == log
    assert "boom" in log.read_text(), "the traceback is kept; only the bad bytes are replaced"


def test_recording_never_raises_when_rendering_the_traceback_fails(tmp_path, monkeypatch):
    """The guard has to be wider than OSError.

    Rendering a traceback runs `repr()` on values this module has never seen, and one of them
    raising must not replace the failure being reported with a worse one. `errors="replace"`
    handles the encoding case, so nothing else pins the guard's width.
    """
    log = tmp_path / "errors.log"

    def _explodes(*_args, **_kwargs):
        raise RecursionError("maximum recursion depth exceeded while getting the repr")

    monkeypatch.setattr(errors.traceback, "format_exception", _explodes)

    assert errors.record(_raise(ValueError("boom")), ["bloomctl"], path=log) is None
    assert not log.exists()


def test_nothing_is_reported_when_the_log_was_created_but_never_written(tmp_path, monkeypatch):
    """Opening the file is not the same as the traceback landing.

    Reporting on the file existing would send someone to an empty log.
    """
    log = tmp_path / "errors.log"

    def _fail(*args, **kwargs):
        raise OSError(errno.EACCES, "Permission denied")

    monkeypatch.setattr(errors.os, "fchmod", _fail)  # after O_CREAT has made the file

    returned = errors.record(_raise(ValueError("boom")), ["bloomctl"], path=log)

    assert returned is None
    assert log.exists() and log.read_text() == "", "the empty file is exactly the trap"


def test_the_log_is_capped_so_it_cannot_grow_forever(tmp_path):
    log = tmp_path / "errors.log"
    log.write_bytes(b"x" * (errors.MAX_LOG_BYTES + 1))

    errors.record(_raise(ValueError("boom")), ["bloomctl"], path=log)

    assert log.stat().st_size < errors.MAX_LOG_BYTES
    assert "boom" in log.read_text()


def test_rotation_keeps_the_filled_log_rather_than_dropping_half_of_it(tmp_path):
    """The old log is moved aside whole, so nothing recorded before the cap is discarded."""
    log = tmp_path / "errors.log"
    log.write_bytes(b"x" * errors.MAX_LOG_BYTES + b"the-oldest-traceback")

    errors.record(_raise(ValueError("boom")), ["bloomctl"], path=log)

    rotated = tmp_path / "errors.log.1"
    assert rotated.exists(), "the filled log is kept, not deleted"
    assert b"the-oldest-traceback" in rotated.read_bytes()
    assert "boom" in log.read_text(), "the new traceback goes to a fresh log"


def test_a_traceback_written_during_a_rotation_is_not_destroyed(tmp_path):
    """The race this rename exists to close: another bloomctl appending mid-rotation.

    A read-then-write-back rotation would write over a traceback recorded after its read.
    A rename touches only the name, so the other process's open handle follows the file.
    """
    log = tmp_path / "errors.log"
    log.write_bytes(b"x" * (errors.MAX_LOG_BYTES + 1))

    with log.open("a", encoding="utf-8") as other_process:
        assert errors._rotate(log) is True
        other_process.write("traceback from the other process")

    rotated = (tmp_path / "errors.log.1").read_text()
    assert "traceback from the other process" in rotated, "the concurrent append was lost"


def test_the_previous_rotated_log_is_replaced_rather_than_accumulating(tmp_path):
    """Two files is the bound; a third would put the cap back where it started."""
    log = tmp_path / "errors.log"
    (tmp_path / "errors.log.1").write_text("from two rotations ago")
    log.write_bytes(b"x" * (errors.MAX_LOG_BYTES + 1))

    assert errors._rotate(log) is True

    assert sorted(p.name for p in tmp_path.iterdir()) == ["errors.log.1"]
    assert "from two rotations ago" not in (tmp_path / "errors.log.1").read_text()


def test_the_rotated_log_is_never_readable_by_others(tmp_path):
    """It carries every traceback and command line the filled log held."""
    log = tmp_path / "errors.log"
    log.write_bytes(b"y" * (errors.MAX_LOG_BYTES + 1))
    log.chmod(0o600)

    errors.record(_raise(ValueError("boom")), ["bloomctl"], path=log)

    rotated = tmp_path / "errors.log.1"
    assert stat.S_IMODE(rotated.stat().st_mode) == 0o600
    assert stat.S_IMODE(log.stat().st_mode) == 0o600


def test_a_failed_rotation_keeps_the_log_it_was_called_to_preserve(tmp_path, monkeypatch):
    """Rotation only runs on a machine that has been failing often enough to fill the log,
    which is when those tracebacks are worth the most."""
    log = tmp_path / "errors.log"
    log.write_bytes(b"y" * (errors.MAX_LOG_BYTES + 1))

    def _no_space(src, dst):
        raise OSError(errno.ENOSPC, "No space left on device")

    monkeypatch.setattr(errors.os, "replace", _no_space)
    rotated = errors._rotate(log)
    monkeypatch.undo()

    assert rotated is False, "the caller must be told, so it does not append past the cap"
    assert log.stat().st_size == errors.MAX_LOG_BYTES + 1, "the log it could not rotate is intact"


def test_the_log_is_not_appended_to_when_it_is_over_cap_and_cannot_be_rotated(
    tmp_path, monkeypatch
):
    """Rotation is what enforces the cap, so appending anyway would grow the log forever."""
    log = tmp_path / "errors.log"
    log.write_bytes(b"y" * (errors.MAX_LOG_BYTES + 1))
    monkeypatch.setattr(errors, "_rotate", lambda _log: False)

    for _ in range(3):
        assert errors.record(_raise(ValueError("boom")), ["bloomctl"], path=log) is None

    assert log.stat().st_size == errors.MAX_LOG_BYTES + 1, "not one byte was added"


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


def test_credentials_in_a_url_option_are_stripped_but_the_host_is_kept():
    """A URL can carry a password. The host is what makes the log worth reading, so only the
    userinfo goes."""
    argv = ["bloomctl", "login", "--api-url", "https://user:pw@bloom.salk.edu/api"]

    assert errors.redact(argv) == ["bloomctl", "login", "--api-url", "https://bloom.salk.edu/api"]
    assert errors.redact(["--server=https://u:p@host:8443"]) == ["--server=https://host:8443"]


def test_an_ordinary_url_survives_redaction_unchanged():
    argv = ["bloomctl", "login", "--api-url", "https://staging.bloom.salk.edu:8443/api"]

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


@pytest.mark.skipif(not hasattr(os, "O_NOFOLLOW"), reason="O_NOFOLLOW is POSIX-only")
def test_a_symlink_left_at_the_log_path_is_refused(tmp_path):
    """The log's name is predictable and it sits in a shared home; following a symlink there
    would let another local user pick the file a traceback lands in."""
    target = tmp_path / "someone-elses-file"
    target.write_text("untouched\n")
    log = tmp_path / "errors.log"
    log.symlink_to(target)

    written = errors.record(_raise(ValueError("boom")), ["bloomctl"], path=log)

    assert written is None
    assert target.read_text() == "untouched\n"


def test_the_console_script_points_at_the_handler_not_the_bare_cli():
    """This one line in pyproject is the only thing that puts the handler in the path.

    Every other test here calls `main` directly, and the rest of the suite drives `cli`
    through CliRunner — so pointing the script back at `bloomctl.cli:cli` would disable
    the whole "a failure is a message, not a stack trace" behaviour with the suite green.
    PyPI uploads are immutable, so this has to be caught before the release, not after.
    """
    import tomllib
    from pathlib import Path as _Path

    pyproject = _Path(__file__).resolve().parents[1] / "pyproject.toml"
    scripts = tomllib.loads(pyproject.read_text(encoding="utf-8"))["project"]["scripts"]

    assert scripts["bloomctl"] == "bloomctl.errors:main"


def test_the_installed_console_script_turns_a_crash_into_one_line(tmp_path):
    """The wiring proved end to end, through the real entry point in a real subprocess."""
    import subprocess
    import sys
    import textwrap

    script = tmp_path / "boom.py"
    script.write_text(
        textwrap.dedent("""
        import click
        from bloomctl import errors

        @click.command()
        def boom():
            raise RuntimeError("nobody planned for this")

        errors.cli = boom
        import bloomctl.cli
        bloomctl.cli.cli = boom
        raise SystemExit(errors.main(args=[]))
        """),
        encoding="utf-8",
    )
    env = {**os.environ, "HOME": str(tmp_path)}

    done = subprocess.run(
        [sys.executable, str(script)], capture_output=True, text=True, env=env, cwd=str(tmp_path)
    )

    assert done.returncode == 1
    assert "Error: nobody planned for this" in done.stderr
    assert "Traceback" not in done.stderr, "the traceback belongs in the log, not on screen"
    assert (tmp_path / ".bloom" / "errors.log").exists()


# --- the handler must survive the failures it exists to report ----------------


def _in_fresh_python(body: str, *, broken: str = ""):
    """Run `body` in a subprocess, optionally with a dependency sabotaged first."""
    import subprocess
    import sys

    return subprocess.run(
        [sys.executable, "-c", broken + body], capture_output=True, text=True
    )


def test_a_half_built_httpx_does_not_escape_the_handler():
    """`httpx 1.0.dev3` imported fine and dropped names this CLI used — that is #629.

    `_is_network_error` reaches for `httpx.TransportError`; guarding only ImportError
    let the AttributeError out of the handler as a stack trace.
    """
    done = _in_fresh_python(
        "from bloomctl import errors\n"
        "print('EXPLAINED:', errors.explain(ValueError('boom')))\n",
        broken=(
            "import sys, types\n"
            "half = types.ModuleType('httpx')\n"  # imports, but has no TransportError
            "sys.modules['httpx'] = half\n"
        ),
    )

    assert "EXPLAINED: boom" in done.stdout, done.stdout + done.stderr
    assert "Traceback" not in done.stderr


class _Hostile(Exception):
    """An exception that raises while being described — `explain()` calls str() on it."""

    def __str__(self):
        raise RuntimeError("__str__ blew up")


def test_an_exception_that_cannot_describe_itself_still_gets_one_line():
    assert errors._describe(_Hostile()) == "_Hostile"


def test_a_failure_that_cannot_describe_itself_still_leaves_main_cleanly(
    _log_in_tmp, monkeypatch, capsys
):
    """Through `main()`, not just the helper — the call site is what protects the user."""

    @click.command()
    def boom():
        raise _Hostile()

    monkeypatch.setattr("bloomctl.cli.cli", boom)

    code = errors.main(args=[])

    err = capsys.readouterr().err
    assert code == 1
    assert "Error: _Hostile" in err
    assert "Traceback" not in err, "describing the failure must not become the failure"


def test_a_dependency_that_fails_to_import_is_a_message_not_a_stack_trace():
    """The CLI's own imports happen inside the guard.

    At module scope a broken dependency lands before any handler exists — which is the
    state a `--pre` install produced, and the reason this module was written.
    """
    done = _in_fresh_python(
        "from bloomctl import errors\n"
        "raise SystemExit(errors.main(args=[]))\n",
        broken=(
            "import sys, types\n"
            "boom = types.ModuleType('dotenv')\n"  # imports, but without dotenv_values
            "sys.modules['dotenv'] = boom\n"
        ),
    )

    assert done.returncode == 1, done.stdout + done.stderr
    assert "Error:" in done.stderr
    assert "Traceback" not in done.stderr, "a broken dependency reached the user as a stack"


# --- the two changes whose failure is silent ---------------------------------


def _pyproject() -> dict:
    import tomllib
    from pathlib import Path as _Path

    return tomllib.loads(
        (_Path(__file__).resolve().parents[1] / "pyproject.toml").read_text(encoding="utf-8")
    )


def test_the_dependency_caps_that_made_the_cli_installable_are_still_there():
    """`httpx 1.0` drops Timeout/HTTPError and `supabase 3.0` drops create_client (#629).

    Nothing else fails when these come off: the suite runs against the installed versions,
    so the first sign would be the release itself, at the moment of an immutable upload.
    """
    deps = " ".join(_pyproject()["project"]["dependencies"])

    assert "httpx>=0.27,<1.0" in deps
    assert "supabase>=2.0.0,<3" in deps


def test_the_credentials_secrets_stay_out_of_their_repr():
    """A traceback carrying a Credentials lands in the file users are told to send us."""
    from bloomctl.credentials import Credentials

    rendered = repr(Credentials("https://x/api", "ANON-KEY-VALUE", "u@s.edu", "hunter2"))

    assert "hunter2" not in rendered
    assert "ANON-KEY-VALUE" not in rendered
    assert "u@s.edu" in rendered, "the rest is what makes a traceback worth reading"
