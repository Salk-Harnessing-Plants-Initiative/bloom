"""Task 3.1 — `bloomctl login` (supabase + /client-info fetch mocked)."""

import pytest
from click.testing import CliRunner

import bloomctl.auth as auth
import bloomctl.credentials as credentials
from bloomctl.cli import cli
from bloomctl.credentials import load_credentials


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    """Redirect credential writes to a tmp config dir."""
    monkeypatch.setattr(credentials, "default_config_dir", lambda: tmp_path / ".bloom")
    return tmp_path / ".bloom"


def test_login_fetches_config_and_writes_creds(cfg, monkeypatch):
    monkeypatch.setattr(
        auth, "fetch_anon_credentials",
        lambda server=auth.DEFAULT_SERVER: ("https://bloom.salk.edu/api", "ANON123"),
    )
    monkeypatch.setattr(auth, "verify_credentials", lambda *a, **k: None)

    result = CliRunner().invoke(cli, ["login"], input="user@salk.edu\nsecret\n")

    assert result.exit_code == 0, result.output
    creds = load_credentials("prod", config_dir=cfg)
    assert creds.email == "user@salk.edu"
    assert creds.password == "secret"
    assert creds.api_url == "https://bloom.salk.edu/api"
    assert creds.anon_key == "ANON123"


def test_login_override_skips_client_info_fetch(cfg, monkeypatch):
    def _boom(*a, **k):
        raise AssertionError("fetch_anon_credentials must not run when overridden")

    monkeypatch.setattr(auth, "fetch_anon_credentials", _boom)
    monkeypatch.setattr(auth, "verify_credentials", lambda *a, **k: None)

    result = CliRunner().invoke(cli, [
        "login", "--api-url", "https://x/api", "--anon-key", "KEY",
        "--email", "a@b.co", "--password", "pw", "--profile", "staging",
    ])

    assert result.exit_code == 0, result.output
    creds = load_credentials("staging", config_dir=cfg)
    assert (creds.api_url, creds.anon_key) == ("https://x/api", "KEY")


def test_login_invalid_credentials_writes_no_file(cfg, monkeypatch):
    monkeypatch.setattr(
        auth, "fetch_anon_credentials",
        lambda server=auth.DEFAULT_SERVER: ("https://x/api", "KEY"),
    )

    def _fail(*a, **k):
        raise auth.AuthError("sign-in failed — check email/password")

    monkeypatch.setattr(auth, "verify_credentials", _fail)

    result = CliRunner().invoke(cli, ["login"], input="user@salk.edu\nbad\n")

    assert result.exit_code != 0
    assert not (cfg / "credentials.txt").exists()
