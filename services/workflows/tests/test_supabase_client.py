"""Unit tests for supabase_client.py's app_client() — credential validation and
the optional postgrest_client_timeout override. Unset (as pipeline.py/video.py
call it) preserves supabase-py's 120s default; dispatch_worker.py opts into a
tighter bound explicitly (see supabase_client.py's module docstring)."""

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


def test_app_client_defaults_to_no_timeout_override(monkeypatch):
    """pipeline.py/video.py call app_client() with no argument — this must NOT
    silently bound their RPCs to the dispatch worker's tighter timeout (they
    have no comparable small-payload guarantee: trigger_pipeline() alone can
    issue up to 200 sequential enqueue_cyl_pipeline_batch calls plus a bulk
    insert of up to MAX_SCAN_IDS=5000 rows)."""
    fake_client = MagicMock()
    fake_client.auth.sign_in_with_password.return_value = MagicMock(session=object())

    with patch("supabase.create_client", return_value=fake_client) as mock_create:
        result = supabase_client.app_client()

    assert result is fake_client
    _, kwargs = mock_create.call_args
    assert kwargs["options"] is None


def test_app_client_passes_an_explicit_postgrest_timeout_when_given(monkeypatch):
    """dispatch_worker.py's wrapped app_client() opts into this explicitly —
    only it has the small-batch guarantee that makes a tight bound safe."""
    fake_client = MagicMock()
    fake_client.auth.sign_in_with_password.return_value = MagicMock(session=object())

    with patch("supabase.create_client", return_value=fake_client) as mock_create:
        result = supabase_client.app_client(
            timeout_seconds=supabase_client.DISPATCH_WORKER_POSTGREST_TIMEOUT_SECONDS
        )

    assert result is fake_client
    _, kwargs = mock_create.call_args
    options = kwargs["options"]
    assert (
        options.postgrest_client_timeout
        == supabase_client.DISPATCH_WORKER_POSTGREST_TIMEOUT_SECONDS
    )
    assert (
        supabase_client.DISPATCH_WORKER_POSTGREST_TIMEOUT_SECONDS < 30
    )  # under stop_grace_period


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
