"""Config-shape tests for the Kong half of the Supabase Studio basic-auth gate.

Studio ships with no authentication of its own, so the only thing standing in
front of it is whatever the edge puts there: Kong's `dashboard` service fronts
`studio:3000` with the `basic-auth` plugin, and that plugin needs a credential
to match. Both are asserted here because each looks fine in isolation — with no
credential Kong rejects everyone, and with no plugin Kong lets everyone through,
and the file reads as correct either way.

Whether the gate actually challenges a request is not asserted here — that is
behaviour, and `tests/integration/test_api_endpoints.py` tests it directly
against a running stack (401 without credentials, 200 with them). These tests
only guard the declarative config, which is why they can run without Docker.

`kong.yml` is parsed with `yaml.safe_load` rather than matched as text: a
substring check over the raw file also matches a commented-out block, so
commenting the plugin or the credential out in place would leave every
assertion green. That is not hypothetical for this file — `kong.yml` already
carries a commented-out `plugins:`/`- name: cors` block, so comment-out is how
a plugin gets disabled here in practice.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
KONG_CONFIG = REPO_ROOT / "volumes" / "api" / "kong.yml"


def _kong_config() -> dict[str, Any]:
    return yaml.safe_load(KONG_CONFIG.read_text(encoding="utf-8"))


def _dashboard_consumer(kong: dict[str, Any]) -> dict[str, Any] | None:
    for consumer in kong.get("consumers") or []:
        if consumer.get("username") == "DASHBOARD":
            return consumer
    return None


def _dashboard_service(kong: dict[str, Any]) -> dict[str, Any] | None:
    for service in kong.get("services") or []:
        if service.get("name") == "dashboard":
            return service
    return None


def test_dashboard_consumer_has_basic_auth_credentials():
    """Kong's `basic-auth` plugin needs a credential on the DASHBOARD consumer.

    Without one the plugin has nothing to match and rejects every request, so
    Studio becomes unreachable rather than gated — a failure that looks
    identical to a correctly-locked-down deployment.
    """
    consumer = _dashboard_consumer(_kong_config())
    assert consumer, "no DASHBOARD consumer in volumes/api/kong.yml"

    credentials = consumer.get("basicauth_credentials")
    assert credentials, (
        "the DASHBOARD consumer declares no `basicauth_credentials`, so Kong's "
        "basic-auth plugin has nothing to match and rejects every request"
    )

    referenced = {(c.get("username"), c.get("password")) for c in credentials}
    assert referenced == {("$DASHBOARD_USERNAME", "$DASHBOARD_PASSWORD")}, (
        "credentials must reference $DASHBOARD_USERNAME/$DASHBOARD_PASSWORD — Kong's "
        "entrypoint expands them from the environment, so hardcoding would commit a "
        f"secret; found: {sorted(referenced)}"
    )


def test_dashboard_credential_is_declared_exactly_once():
    """Kong accepts credentials nested under a consumer *or* as a top-level
    `basicauth_credentials:` list keyed by `consumer:`. Declaring both is valid
    YAML and boots without complaint, but which one Kong ends up honouring is
    not something this repo has established — so a password change to the wrong
    block may be a no-op that looks applied. One declaration, nested under the
    consumer (matching the three key-auth consumers above it), keeps that
    ambiguity out of the file.
    """
    kong = _kong_config()

    assert "basicauth_credentials" not in kong, (
        "a top-level `basicauth_credentials:` block duplicates the credential nested "
        "under the DASHBOARD consumer; which one Kong honours is ambiguous, so edits "
        "to the other may silently do nothing"
    )

    declaring = [
        c.get("username")
        for c in kong.get("consumers") or []
        if c.get("basicauth_credentials")
    ]
    assert declaring == ["DASHBOARD"], (
        "expected exactly one consumer declaring `basicauth_credentials` (DASHBOARD), "
        f"found: {declaring}"
    )

    credentials = _dashboard_consumer(kong)["basicauth_credentials"]
    assert len(credentials) == 1, (
        f"expected exactly one credential on the DASHBOARD consumer, found "
        f"{len(credentials)} — a second one is another silently-ignored edit target"
    )


def test_dashboard_service_still_has_the_basic_auth_plugin():
    """The credential is inert unless the `dashboard` service actually enables
    the plugin — losing the plugin would leave Studio open with the credential
    still present and the config still looking correct.

    `enabled` and `anonymous` are checked explicitly because both leave the
    plugin looking present while it gates nothing: `enabled: false` switches it
    off, and `anonymous` makes Kong forward credential-less requests as that
    consumer instead of returning 401.
    """
    service = _dashboard_service(_kong_config())
    assert service, "no `dashboard` service in volumes/api/kong.yml"

    plugins = service.get("plugins") or []
    basic_auth = [p for p in plugins if p.get("name") == "basic-auth"]
    assert basic_auth, (
        "the `dashboard` service no longer enables the `basic-auth` plugin, so the "
        "DASHBOARD credential gates nothing; plugins present: "
        f"{[p.get('name') for p in plugins]}"
    )
    assert all(p.get("enabled", True) for p in basic_auth), (
        "the `dashboard` service's `basic-auth` plugin is declared but disabled "
        "(`enabled: false`), which leaves Studio served unauthenticated"
    )
    assert not any("anonymous" in (p.get("config") or {}) for p in basic_auth), (
        "the `dashboard` service's `basic-auth` plugin sets `anonymous`, so Kong "
        "forwards credential-less requests as that consumer instead of returning 401"
    )
