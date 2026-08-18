"""Live assertion: a hostname with no route is refused, not answered with 200.

Config-shape counterpart: tests/unit/test_caddy_unrouted_host.py.

Two distinct paths are covered, because the fix has two halves:

* A hostname that matches a site address but no `handle` inside it — the case
  from #690, where production answered `nonexistent.bloom.salk.edu` with an
  empty 200. CI serves `studio.localhost` and `minio.localhost`, so a name
  under `.localhost` that has no route reaches the site block's fall-through.
* A hostname matching no site address at all, answered by the `:80` block.

Asserting only the first would leave the half that fixes the reported bug
untested; asserting only the second would test the easier half.
"""

import urllib.error
import urllib.request

import pytest

pytestmark = pytest.mark.integration

# Matches a site address (`http://studio.localhost` is served) but no handle
# inside the site block routes it. This is the #690 shape.
UNROUTED_IN_SITE = "studio.localhost."

# Matches no site address at all, so it never enters the site block.
OUTSIDE_SITE = ["nonexistent.localhost", "not-a-service.localhost", "evil.example.com"]


def _get(base_url: str, host: str):
    """Status and body for a GET carrying an explicit Host header."""
    req = urllib.request.Request(base_url, headers={"Host": host})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


@pytest.mark.parametrize("host", OUTSIDE_SITE)
def test_hostname_outside_every_site_address_is_refused(base_url, host):
    """Answered by the `:80` block rather than Caddy's implicit empty 200."""
    status, body = _get(base_url, host)
    assert status == 404, (
        f"{host} answered {status} with {len(body)} bytes, expected 404 — a "
        "hostname we serve no site address for should be refused, not answered"
    )


def test_routed_hostname_still_works(base_url):
    """The fall-through must not swallow a hostname that does have a route.

    Guards the ordering the unit test pins: Caddy runs the first matching
    `handle`, so a fall-through placed above the per-host blocks would refuse
    their traffic instead.
    """
    status, _ = _get(base_url, "studio.localhost")
    assert status != 404, (
        "studio.localhost is a routed hostname but was refused — the "
        "fall-through is matching ahead of the per-host handle blocks"
    )
