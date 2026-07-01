"""Task 1.2 — the CLI exposes its commands."""

from click.testing import CliRunner

from bloomctl.cli import cli


def test_root_help_lists_commands():
    result = CliRunner().invoke(cli, ["--help"])
    assert result.exit_code == 0
    assert "login" in result.output


def test_login_help_exits_zero():
    result = CliRunner().invoke(cli, ["login", "--help"])
    assert result.exit_code == 0
