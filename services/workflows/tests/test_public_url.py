"""Unit tests for rewriting signed URLs from the internal Supabase host to the
public base (video._to_public_url), mirroring web/lib/supabase/storage-url.ts."""

import video

INTERNAL = "http://kong:8000"
PUBLIC = "https://bloom.salk.edu/api"
SIGNED = "/storage/v1/object/sign/videos/cyl-videos/1.mp4?token=abc"


def _configure(monkeypatch, internal=INTERNAL, public=PUBLIC):
    monkeypatch.setattr(video, "SUPABASE_URL", internal)
    monkeypatch.setattr(video, "PUBLIC_SUPABASE_URL", public)


def test_rewrites_internal_host(monkeypatch):
    _configure(monkeypatch)
    assert video._to_public_url(INTERNAL + SIGNED) == PUBLIC + SIGNED


def test_noop_when_public_base_unset(monkeypatch):
    _configure(monkeypatch, public=None)
    assert video._to_public_url(INTERNAL + SIGNED) == INTERNAL + SIGNED


def test_noop_when_internal_base_unset(monkeypatch):
    _configure(monkeypatch, internal=None)
    assert video._to_public_url(INTERNAL + SIGNED) == INTERNAL + SIGNED


def test_only_replaces_leading_host(monkeypatch):
    # An internal host later in the string (e.g. a redirect query param) is untouched.
    _configure(monkeypatch)
    url = INTERNAL + "/storage/v1/x?redirect=" + INTERNAL + "/y"
    assert video._to_public_url(url) == PUBLIC + "/storage/v1/x?redirect=" + INTERNAL + "/y"


def test_strips_trailing_slashes(monkeypatch):
    # Trailing slashes on either base must not produce a double slash.
    _configure(monkeypatch, internal=INTERNAL + "/", public=PUBLIC + "/")
    assert video._to_public_url(INTERNAL + SIGNED) == PUBLIC + SIGNED


def test_derives_internal_host_from_supabase_url(monkeypatch):
    # Not hardcoded to "kong": whatever SUPABASE_URL is set to is the host swapped.
    _configure(monkeypatch, internal="http://gateway:9000")
    assert video._to_public_url("http://gateway:9000" + SIGNED) == PUBLIC + SIGNED


def test_leaves_non_internal_url_untouched(monkeypatch):
    _configure(monkeypatch)
    url = "https://already-public.example/storage/v1/x"
    assert video._to_public_url(url) == url


def test_handles_none(monkeypatch):
    _configure(monkeypatch)
    assert video._to_public_url(None) is None
