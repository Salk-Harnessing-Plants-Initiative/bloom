"""CI must serve a hostname that has no route, or the coverage is vacuous.

`tests/integration/test_caddy_unrouted_host.py` proves that a hostname inside the
site block with no `handle` gets refused rather than answered — the production
case from #690. Reaching that code path requires CI to serve such a hostname:
every other site address has a `handle`, and a name matching no site address is
answered by the `:80` block instead, which is a different path.

Drop `unrouted.localhost` from the workflow and those tests keep passing while
testing nothing, which is exactly the failure this repo already hit once with
`test_studio_reachable`. This pins it.
"""

from __future__ import annotations

import re
from pathlib import Path

WORKFLOW = Path(__file__).resolve().parents[2] / ".github" / "workflows" / "pr-checks.yml"

# Serves the app, Studio, and the MinIO console; each has a `handle` block.
ROUTED_HOSTS = ["http://localhost", "http://studio.localhost", "http://minio.localhost"]

# Served on purpose with no `handle`, so it reaches the site block's fall-through.
UNROUTED_HOST = "http://unrouted.localhost"


def _site_addresses() -> str:
    text = WORKFLOW.read_text(encoding="utf-8")
    line = re.search(r'CADDY_SITE_ADDRESSES=([^"\n]+)', text)
    assert line, "CADDY_SITE_ADDRESSES is not set in .github/workflows/pr-checks.yml"
    return line.group(1)


def test_ci_serves_every_routed_hostname():
    """Without these, per-hostname routing is never exercised at all."""
    addresses = _site_addresses()
    missing = [h for h in ROUTED_HOSTS if h not in addresses]
    assert not missing, (
        f"CI does not serve {missing}. A Host matching no site address never enters "
        f"the site block, so its routing goes untested: {addresses!r}"
    )


def test_ci_serves_a_hostname_with_no_route():
    """The one host that reaches the site block's fall-through."""
    addresses = _site_addresses()
    assert UNROUTED_HOST in addresses, (
        f"CI does not serve {UNROUTED_HOST}, so nothing reaches the site block's "
        "fall-through — the path that fixes the wildcard case on production. The "
        f"unrouted-host tests would still pass while proving nothing: {addresses!r}"
    )
