"""Config-shape tests for the Kong half of the Supabase Studio basic-auth gate.

Studio ships with no authentication of its own, so the only thing standing in
front of it is whatever the edge puts there: Kong's `dashboard` service fronts
`studio:3000` with the `basic-auth` plugin, and that plugin needs a credential
to match. Both are asserted here because each looks fine in isolation — with no
credential Kong rejects everyone, and with no plugin Kong lets everyone through,
and the file reads as correct either way.

The Caddy half is asserted too: the gate only applies to traffic Caddy actually
sends through Kong, and `tests/integration/test_api_endpoints.py` cannot stand in
for that — it requests `/` only, so a route added above the catch-all that reaches
`studio:3000` directly is invisible to it, to the deploy smoke probe, and to every
other test in the repo.

That assertion is an allowlist (every upstream must BE Kong), not a denylist on
`studio:`. An earlier denylist form was removed because `reverse_proxy
http://studio:3000` walked straight past it — but the fix for an evadable denylist
is to invert it, not to drop the coverage: an allowlist rejects every spelling of
every non-Kong destination, including ones nobody has thought of yet.

These tests guard declarative config only, which is why they run without Docker.

`kong.yml` is parsed with `yaml.safe_load` rather than matched as text: a
substring check over the raw file also matches a commented-out block, so
commenting the plugin or the credential out in place would leave every
assertion green. That is not hypothetical for this file — `kong.yml` already
carries a commented-out `plugins:`/`- name: cors` block, so comment-out is how
a plugin gets disabled here in practice.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

# Caddyfile comments are `#`-to-end-of-line like the shell snippets this helper
# was written for. Stripping them first means a commented-out directive can
# neither satisfy an assertion nor hide a live one below it.
from tests.unit._workflow_helpers import _strip_line_comment

REPO_ROOT = Path(__file__).resolve().parents[2]
CADDYFILE = REPO_ROOT / "caddy" / "Caddyfile"
KONG_CONFIG = REPO_ROOT / "volumes" / "api" / "kong.yml"

KONG_UPSTREAM = "kong:{$KONG_PORT}"


def _braced(text: str, open_brace: int) -> str | None:
    """Body of the block whose `{` sits at `open_brace`, by matching braces.

    Depth counting tolerates `{$KONG_PORT}`-style placeholders, which are
    balanced and would otherwise have to be special-cased.
    """
    depth = 0
    for i in range(open_brace, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                return text[open_brace + 1 : i]
    return None


def _studio_block() -> str | None:
    """The `handle @studio` body, with comments stripped."""
    caddyfile = CADDYFILE.read_text(encoding="utf-8")
    uncommented = "\n".join(_strip_line_comment(line) for line in caddyfile.splitlines())
    header = re.search(r"handle\s+@studio\b", uncommented)
    if not header:
        return None
    open_brace = uncommented.find("{", header.end())
    return None if open_brace == -1 else _braced(uncommented, open_brace)


def _reverse_proxy_upstreams(block: str) -> list[str]:
    """Every upstream named by a `reverse_proxy` directive in `block`.

    Only a *trailing* `{` is stripped (it opens the directive's option block).
    Slicing at the first `{` instead would truncate `kong:{$KONG_PORT}` to
    `kong:` and let any port through. A directive naming no upstream inline
    yields `""`, which fails the allowlist — Caddy allows the upstream to be
    given inside the block via `to ...`, and this repo never does, so that
    shape is rejected rather than parsed.
    """
    upstreams = []
    for line in block.splitlines():
        m = re.search(r"\breverse_proxy\b(.*)$", line)
        if not m:
            continue
        rest = m.group(1).strip()
        if rest.endswith("{"):
            rest = rest[:-1].strip()
        upstreams.extend(rest.split() or [""])
    return upstreams


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


def test_every_studio_route_proxies_kong():
    """No route under `handle @studio` may reach studio directly.

    Studio ships with no authentication, so a route that reaches `studio:3000`
    without traversing Kong is served to anyone who can resolve the hostname —
    including `/api/platform/pg-meta/*`, which executes arbitrary SQL.

    Asserted as an allowlist over every `reverse_proxy` in the block, not as a
    check on the catch-all alone: a `handle_path /_next/*` added above it would
    bypass the gate for every asset request while the catch-all still looked
    correct, and no other test in the repo would notice (the integration test
    and the deploy smoke probe both request `/` only).
    """
    studio = _studio_block()
    assert studio is not None, "missing `handle @studio` block in caddy/Caddyfile"

    upstreams = _reverse_proxy_upstreams(studio)
    assert upstreams, "no `reverse_proxy` at all under `handle @studio`"

    foreign = sorted({u for u in upstreams if u != KONG_UPSTREAM})
    assert not foreign, (
        f"every `reverse_proxy` under `handle @studio` must name `{KONG_UPSTREAM}` so "
        "Kong's basic-auth gate applies to every path on the Studio hostname; these "
        f"bypass it: {foreign}"
    )


def test_studio_has_exactly_one_catch_all_reaching_kong():
    """The catch-all is what gates every path no other route claims.

    Separate from the allowlist above, which is vacuously true if the catch-all
    is deleted entirely. Exactly one, because Caddy runs matcher-less handlers
    in written order — a second is dead config a reader will mistake for the
    live route.
    """
    studio = _studio_block()
    assert studio is not None, "missing `handle @studio` block in caddy/Caddyfile"

    # Depth 0 only: a `handle {` nested inside a `handle_path /x/* { ... }` is
    # not the UI catch-all, and counting it would let a decoy stand in.
    catch_alls = [
        m.end() - 1
        for m in re.finditer(r"\bhandle\s*\{", studio)
        if studio.count("{", 0, m.start()) == studio.count("}", 0, m.start())
    ]
    assert len(catch_alls) == 1, (
        f"expected exactly one matcher-less `handle {{ ... }}` under `handle @studio` "
        f"(the UI catch-all), found {len(catch_alls)}"
    )

    body = _braced(studio, catch_alls[0])
    assert body is not None, "unbalanced braces in the Studio catch-all block"
    assert re.search(r"reverse_proxy\s+kong:\{\$KONG_PORT\}", body), (
        "the Studio UI catch-all must proxy `kong:{$KONG_PORT}` so unmatched paths "
        f"are gated rather than unrouted; found: {body.strip()!r}"
    )


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
