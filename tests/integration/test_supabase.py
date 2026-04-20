"""
Supabase connection tests — verify auth, RLS, and storage work end-to-end.

Prerequisites:
  1. Compose stack running: docker compose -f docker-compose.prod.yml --env-file .env.prod up -d
  2. Database migrations applied (species table, RLS policies, etc.)
  3. Storage buckets created (minio-init container runs automatically on compose up)

Run: python -m pytest tests/integration/test_supabase.py -v
"""

import os
import pytest
import uuid
import json

pytestmark = pytest.mark.integration


# --- Auth Tests ---

def test_anon_key_is_valid(api, anon_key):
    """Anon key authenticates against PostgREST."""
    status, body = api("/api/rest/v1/", api_key=anon_key)
    assert status == 200


def test_service_role_key_is_valid(api, service_role_key):
    """Service role key authenticates against PostgREST."""
    status, body = api("/api/rest/v1/", api_key=service_role_key)
    assert status == 200


def test_signup_creates_user(api, anon_key):
    """Sign up via GoTrue creates a new user."""
    email = f"ci-test-{uuid.uuid4().hex[:8]}@salk.edu"
    status, body = api(
        "/api/auth/v1/signup",
        api_key=anon_key,
        method="POST",
        data={"email": email, "password": "testpassword123!"},
    )
    assert status in (200, 201), f"Signup failed: {body}"
    assert "id" in body or "user" in body


def test_signin_returns_session(api, anon_key):
    """Sign up then sign in returns a valid JWT session."""
    email = f"ci-test-{uuid.uuid4().hex[:8]}@salk.edu"
    # Sign up first (auto-confirm is enabled in dev/ci)
    api(
        "/api/auth/v1/signup",
        api_key=anon_key,
        method="POST",
        data={"email": email, "password": "testpassword123!"},
    )
    # Sign in
    status, body = api(
        "/api/auth/v1/token?grant_type=password",
        api_key=anon_key,
        method="POST",
        data={"email": email, "password": "testpassword123!"},
    )
    assert status == 200, f"Signin failed: {body}"
    assert "access_token" in body
    assert "refresh_token" in body


# --- RLS Tests ---

def test_anon_can_select_public_tables(api, anon_key):
    """Anon key can read from public tables."""
    status, body = api("/api/rest/v1/species?select=id,common_name&limit=1", api_key=anon_key)
    assert status == 200
    assert isinstance(body, list)


def test_anon_cannot_insert_without_auth(api, anon_key):
    """Anon key cannot insert into protected tables."""
    status, body = api(
        "/api/rest/v1/species",
        api_key=anon_key,
        method="POST",
        data={"common_name": "ci-test-species", "genus": "Test", "species": "testicus"},
    )
    assert status in (401, 403), f"Expected 401/403 but got {status}: {body}"


# --- Storage Tests ---

def test_storage_upload_download_delete(api, service_role_key):
    """Upload a file, download it, then delete it."""
    bucket = "images"
    filename = f"ci-test-{uuid.uuid4().hex[:8]}.txt"
    content = "integration test file content"

    # Upload
    import urllib.request
    url = f"http://localhost/api/storage/v1/object/{bucket}/{filename}"
    headers = {
        "apikey": service_role_key,
        "Authorization": f"Bearer {service_role_key}",
        "Content-Type": "text/plain",
    }
    req = urllib.request.Request(url, data=content.encode(), headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            upload_status = resp.status
    except urllib.error.HTTPError as e:
        pytest.fail(f"Upload failed with {e.code}: {e.read().decode()}")
    assert upload_status == 200

    # Download
    req = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            downloaded = resp.read().decode()
    except urllib.error.HTTPError as e:
        pytest.fail(f"Download failed with {e.code}: {e.read().decode()}")
    assert downloaded == content

    # Delete
    req = urllib.request.Request(url, headers=headers, method="DELETE")
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            delete_status = resp.status
    except urllib.error.HTTPError as e:
        # 200 or 204 are both fine
        delete_status = e.code
    assert delete_status in (200, 204)
