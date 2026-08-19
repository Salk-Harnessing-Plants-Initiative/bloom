"""Config-shape test for gating Supabase Studio behind Kong's basic-auth.

Studio ships with no authentication of its own, so the only thing standing in
front of it is whatever the edge puts there. Kong already declares a
`dashboard` service fronting `studio:3000` with the `basic-auth` plugin — but
the plugin needs a credential to match, and the Caddy route has to actually
send Studio traffic through Kong for the plugin to run at all. Either half
missing is silent: with no credential Kong rejects everyone, and with a direct
`reverse_proxy studio` the gate is simply never reached.

Both halves are asserted here because each looks fine in isolation.

`kong.yml` is parsed with `yaml.safe_load`, and the Caddyfile has its comments
stripped before matching, because a substring check over raw file text also
matches a commented-out block. Commenting the `basic-auth` plugin out in place
leaves Studio served unauthenticated; commenting the credential out leaves Kong
rejecting everyone — and against raw text both mutations keep every assertion
here green, including the "declared exactly once" count, which would score the
commented-out line as the one live declaration. That is not hypothetical for
this file: `kong.yml` already carries a commented-out `plugins:`/`- name: cors`
block, so comment-out is how a plugin gets disabled here in practice.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

# Caddyfile comments use the same `#`-to-end-of-line form as the shell snippets
# this helper was written for, and defeating comment-out is the same job.
from tests.unit._workflow_helpers import _strip_line_comment

REPO_ROOT = Path(__file__).resolve().parents[2]
CADDYFILE = REPO_ROOT / "caddy" / "Caddyfile"
KONG_CONFIG = REPO_ROOT / "volumes" / "api" / "kong.yml"


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


def _block_after(text: str, header_pattern: str) -> str | None:
    """Body of the first `<header> {` directive, sliced by matching braces."""
    header = re.search(header_pattern, text)
    if not header:
        return None
    open_brace = text.find("{", header.end())
    return None if open_brace == -1 else _braced(text, open_brace)


def _studio_block() -> str | None:
    """The `handle @studio` body, with comments stripped.

    Stripping first means a commented-out directive can neither satisfy an
    assertion nor hide a live one below it.
    """
    caddyfile = CADDYFILE.read_text(encoding="utf-8")
    uncommented = "\n".join(_strip_line_comment(line) for line in caddyfile.splitlines())
    return _block_after(uncommented, r"handle\s+@studio\b")


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


def test_studio_ui_is_proxied_through_kong():
    """Nothing under `handle @studio` may reach studio directly.

    The catch-all is what carries the basic-auth gate, so it must proxy Kong.
    Asserting only on the catch-all is not enough: a `handle_path /_next/*`
    added above it would take Studio's assets straight to `studio:3000`,
    bypassing the gate for every asset request while the catch-all still looks
    correct. Asset-path handling is this change's own stated blast radius, so
    that is the likeliest next edit — hence the assertion below covers the
    whole block, not just the catch-all.
    """
    studio = _studio_block()
    assert studio is not None, "missing `handle @studio` block in caddy/Caddyfile"

    # `handle {` with no matcher and no path is the UI catch-all; the routed
    # blocks above it are all `handle_path /prefix/*`.
    plain = [m for m in re.finditer(r"\bhandle\s*\{", studio)]
    assert plain, "no catch-all `handle { ... }` found under `handle @studio`"

    body = _braced(studio, plain[-1].end() - 1)
    assert body is not None, "unbalanced braces in the Studio catch-all block"
    # The port is part of the assertion: a bare `kong:` would also match a proxy
    # to the wrong port, matching test_caddy_client_info_route.py's reasoning.
    assert re.search(r"reverse_proxy\s+kong:\{\$KONG_PORT\}", body), (
        "the Studio UI catch-all must proxy `kong:{$KONG_PORT}` so the basic-auth "
        f"plugin on its `dashboard` service applies; found: {body.strip()!r}"
    )

    direct = re.findall(r"reverse_proxy\s+studio:\S*", studio)
    assert not direct, (
        "no route under `handle @studio` may proxy `studio:` directly — it skips "
        f"Kong's basic-auth gate for every path it matches; found: {direct}"
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

    `enabled` is checked explicitly: Kong treats a plugin entry with
    `enabled: false` as declared-but-off, which reads as present to anything
    that only looks for the name.
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
