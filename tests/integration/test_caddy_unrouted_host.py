"""Live assertion: a hostname with no route is refused, not answered with 200.

Config-shape counterpart: tests/unit/test_caddy_unrouted_host.py.

Two distinct paths, because the fix has two halves and they are easy to confuse:

* `unrouted.localhost` is a **served site address with no handle block**, so it
  reaches the site block and falls out the bottom. That is the shape from #690,
  where production answered `nonexistent.bloom.salk.edu` with an empty 200. CI
  serves this hostname for no other reason than to exercise this path.
* The others match no site address at all and never enter the site block; they
  are answered by the `:80` block.

Covering only the second would leave the half that fixes the reported bug
untested — which is what an earlier version of this file did.
"""

import urllib.error
import urllib.request

import pytest

pytestmark = pytest.mark.integration

# Served, but deliberately unrouted — reaches the site block's fall-through.
UNROUTED_IN_SITE = "unrouted.localhost"

# Match no site address, so they never enter the site block.
OUTSIDE_SITE = ["nonexistent.localhost", "not-a-service.localhost", "evil.example.com"]


def _get(base_url: str, host: str):
    """Status and body for a GET carrying an explicit Host header."""
    req = urllib.request.Request(base_url, headers={"Host": host})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def test_served_hostname_with_no_route_is_refused(base_url):
    """The #690 case: inside the site block, matched by no handle."""
    status, body = _get(base_url, UNROUTED_IN_SITE)
    assert status == 404, (
        f"{UNROUTED_IN_SITE} answered {status} with {len(body)} bytes, expected 404. "
        "This hostname is a site address with no handle block, so it reaches the "
        "site block's fall-through — the path that fixes the wildcard case on prod."
    )


@pytest.mark.parametrize("host", OUTSIDE_SITE)
def test_hostname_outside_every_site_address_is_refused(base_url, host):
    """Answered by the `:80` block rather than Caddy's implicit empty 200."""
    status, body = _get(base_url, host)
    assert status == 404, (
        f"{host} answered {status} with {len(body)} bytes, expected 404 — a "
        "hostname we serve no site address for should be refused, not answered"
    )


@pytest.mark.parametrize("host", ["localhost", "studio.localhost", "minio.localhost"])
def test_routed_hostnames_are_not_swallowed(base_url, host):
    """A routed hostname must still reach its handler, not the fall-through.

    404 is the discriminator here, not success. `studio.localhost` answers 401
    from Kong's basic-auth gate (#689), and that 401 is itself proof the request
    traversed the `@studio` handle rather than falling out the bottom of the site
    block — so asserting a 2xx would fail on a correctly gated console.
    """
    status, body = _get(base_url, host)
    assert status != 404, (
        f"{host} is a routed hostname but answered 404 with {len(body)} bytes — "
        "the fall-through matched ahead of the per-host handle blocks"
    )
    assert status < 500, (
        f"{host} answered {status} with {len(body)} bytes — the handler was reached "
        "but its upstream is down"
    )
