"""Live assertion: an unrouted hostname is refused, not answered with an empty 200.

Config-shape counterpart: tests/unit/test_caddy_unrouted_host.py.
"""

import urllib.error
import urllib.request

import pytest

from tests.integration.conftest import BASE_URL

pytestmark = pytest.mark.integration


def _get(host: str):
    """Status and body for a GET carrying an explicit Host header."""
    req = urllib.request.Request(BASE_URL, headers={"Host": host})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


@pytest.mark.parametrize(
    "host",
    ["nonexistent.localhost", "not-a-service.localhost", "evil.example.com"],
)
def test_unrouted_hostname_is_refused(host):
    """Caddy answers a Host it serves no route for with an empty `200 OK`.

    That is a success code for a page that does not exist, and it is why a
    status-only check can pass against nothing. Asserting the status alone here
    would reproduce the very bug being fixed, so the body is asserted too.
    """
    status, body = _get(host)
    assert status == 404, (
        f"{host} answered {status} with {len(body)} bytes, expected 404. An empty "
        "200 means the request fell out of the site block and Caddy answered it "
        "implicitly — a hostname with no route should be refused."
    )
    assert body, f"{host} returned 404 with an empty body; expected a reason phrase"
