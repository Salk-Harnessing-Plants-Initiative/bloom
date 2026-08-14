"""Unit tests for supabase_client.py's app_client() — credential validation and
the explicit postgrest_client_timeout override (see supabase_client.py's
module docstring on POSTGREST_TIMEOUT_SECONDS for why: the library default of
120s exceeds this worker's stop_grace_period)."""

from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException

import supabase_client


@pytest.fixture(autouse=True)
def _configured(monkeypatch):
    monkeypatch.setattr(supabase_client, "SUPABASE_URL", "http://kong:8000")
    monkeypatch.setattr(supabase_client, "SUPABASE_ANON_KEY", "anon-key")
    monkeypatch.setattr(supabase_client, "APP_EMAIL", "workflows@example.com")
    monkeypatch.setattr(supabase_client, "APP_PASSWORD", "test-pw")


def test_missing_config_raises_500_before_importing_supabase(monkeypatch):
    monkeypatch.setattr(supabase_client, "SUPABASE_URL", None)
    with pytest.raises(HTTPException) as exc:
        supabase_client.app_client()
    assert exc.value.status_code == 500
    assert "SUPABASE_URL" in exc.value.detail


def test_app_client_passes_an_explicit_postgrest_timeout(monkeypatch):
    """The library default (120s) exceeds this worker's stop_grace_period
    (docker-compose.{dev,prod}.yml, 30s) — app_client() must construct
    ClientOptions with an explicit, bounded postgrest_client_timeout rather
    than relying on the implicit 120s default."""
    fake_client = MagicMock()
    fake_client.auth.sign_in_with_password.return_value = MagicMock(session=object())

    with patch("supabase.create_client", return_value=fake_client) as mock_create:
        result = supabase_client.app_client()

    assert result is fake_client
    _, kwargs = mock_create.call_args
    options = kwargs["options"]
    assert options.postgrest_client_timeout == supabase_client.POSTGREST_TIMEOUT_SECONDS
    assert supabase_client.POSTGREST_TIMEOUT_SECONDS < 30  # under stop_grace_period


def test_sign_in_failure_raises_500(monkeypatch):
    fake_client = MagicMock()
    fake_client.auth.sign_in_with_password.side_effect = RuntimeError("network error")

    with patch("supabase.create_client", return_value=fake_client):
        with pytest.raises(HTTPException) as exc:
            supabase_client.app_client()
    assert exc.value.status_code == 500


def test_no_session_raises_500(monkeypatch):
    fake_client = MagicMock()
    fake_client.auth.sign_in_with_password.return_value = MagicMock(session=None)

    with patch("supabase.create_client", return_value=fake_client):
        with pytest.raises(HTTPException) as exc:
            supabase_client.app_client()
    assert exc.value.status_code == 500
