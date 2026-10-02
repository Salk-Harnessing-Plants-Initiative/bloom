"""Per-test isolation for state that lives in a module."""

import pytest


@pytest.fixture(autouse=True)
def _forget_rate_limit_hits():
    """Clear the rate limiter between tests.

    It counts per user per process, and the tests share both. Four of them make
    a real call against a default limit of five, so a fifth would start failing
    the suite for a reason that has nothing to do with what it tests.
    """
    import auth

    with auth._hits_lock:
        auth._hits.clear()
    yield
    with auth._hits_lock:
        auth._hits.clear()


class _NoRealS3:
    """httpx as the folder check sees it, refusing a client that would reach real S3."""

    def __getattr__(self, name):
        import httpx

        return getattr(httpx, name)

    @staticmethod
    def Client(*args, **kwargs):
        import httpx

        if "transport" not in kwargs:
            raise AssertionError(
                "a test reached for real S3; pass a client or patch it"
            )
        return httpx.Client(*args, **kwargs)


@pytest.fixture(autouse=True)
def _no_real_s3(monkeypatch):
    """Fail any test that would list a real S3 folder."""
    import s3_folder

    monkeypatch.setattr(s3_folder, "httpx", _NoRealS3())
