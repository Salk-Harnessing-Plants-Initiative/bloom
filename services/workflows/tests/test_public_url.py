"""Unit tests for rewriting signed URLs from the internal kong host to the
public base (video._to_public_url), mirroring web/lib/supabase/storage-url.ts."""

import video

PUBLIC = "https://bloom.salk.edu/api"
SIGNED = "/storage/v1/object/sign/videos/cyl-videos/1.mp4?token=abc"


def test_rewrites_internal_kong_host(monkeypatch):
    monkeypatch.setattr(video, "PUBLIC_SUPABASE_URL", PUBLIC)
    assert video._to_public_url("http://kong:8000" + SIGNED) == PUBLIC + SIGNED


def test_rewrites_https_kong_host(monkeypatch):
    monkeypatch.setattr(video, "PUBLIC_SUPABASE_URL", PUBLIC)
    assert video._to_public_url("https://kong:8000" + SIGNED) == PUBLIC + SIGNED


def test_noop_when_public_base_unset(monkeypatch):
    monkeypatch.setattr(video, "PUBLIC_SUPABASE_URL", None)
    url = "http://kong:8000" + SIGNED
    assert video._to_public_url(url) == url


def test_only_replaces_leading_host(monkeypatch):
    # A kong host later in the string (e.g. a redirect query param) is untouched.
    monkeypatch.setattr(video, "PUBLIC_SUPABASE_URL", PUBLIC)
    url = "http://kong:8000/storage/v1/x?redirect=http://kong:8000/y"
    assert video._to_public_url(url) == PUBLIC + "/storage/v1/x?redirect=http://kong:8000/y"


def test_leaves_non_kong_url_untouched(monkeypatch):
    monkeypatch.setattr(video, "PUBLIC_SUPABASE_URL", PUBLIC)
    url = "https://already-public.example/storage/v1/x"
    assert video._to_public_url(url) == url


def test_handles_none(monkeypatch):
    monkeypatch.setattr(video, "PUBLIC_SUPABASE_URL", PUBLIC)
    assert video._to_public_url(None) is None
