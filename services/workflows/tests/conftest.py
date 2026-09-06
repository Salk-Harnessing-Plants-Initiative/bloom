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
