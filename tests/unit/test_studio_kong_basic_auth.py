"""Config-shape test for gating Supabase Studio behind Kong's basic-auth.

Studio ships with no authentication of its own, so the only thing standing in
front of it is whatever the edge puts there. Kong already declares a
`dashboard` service fronting `studio:3000` with the `basic-auth` plugin — but
the plugin needs a credential to match, and the Caddy route has to actually
send Studio traffic through Kong for the plugin to run at all. Either half
missing is silent: with no credential Kong rejects everyone, and with a direct
`reverse_proxy studio` the gate is simply never reached.

Both halves are asserted here because each looks fine in isolation.
"""

from __future__ import annotations

import re
from pathlib import Path

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
    return _block_after(CADDYFILE.read_text(encoding="utf-8"), r"handle\s+@studio\b")


def test_studio_ui_is_proxied_through_kong():
    """The catch-all under `handle @studio` must reach Kong, not studio directly.

    The backend routes above it (`/auth/*`, `/rest/*`, ...) keep their own
    key-auth Kong routes; it is the UI catch-all that carries the basic-auth
    gate, and pointing it straight at `studio:3000` bypasses it entirely.
    """
    studio = _studio_block()
    assert studio is not None, "missing `handle @studio` block in caddy/Caddyfile"

    # `handle {` with no matcher and no path is the UI catch-all; the routed
    # blocks above it are all `handle_path /prefix/*`.
    plain = [m for m in re.finditer(r"\bhandle\s*\{", studio)]
    assert plain, "no catch-all `handle { ... }` found under `handle @studio`"

    body = _braced(studio, plain[-1].end() - 1)
    assert body is not None, "unbalanced braces in the Studio catch-all block"
    assert re.search(r"reverse_proxy\s+kong:", body), (
        "the Studio UI catch-all must proxy Kong so the basic-auth plugin on its "
        f"`dashboard` service applies; found: {body.strip()!r}"
    )
    assert not re.search(r"reverse_proxy\s+studio:", body), (
        "proxying `studio:` directly skips Kong's basic-auth gate entirely"
    )


def test_dashboard_consumer_has_basic_auth_credentials():
    """Kong's `basic-auth` plugin needs a credential on the DASHBOARD consumer.

    Without one the plugin has nothing to match and rejects every request, so
    Studio becomes unreachable rather than gated — a failure that looks
    identical to a correctly-locked-down deployment.
    """
    kong = KONG_CONFIG.read_text(encoding="utf-8")
    # Stop at the next *consumer* (two-space indent), not at the nested
    # `- username:` inside this consumer's own credentials list.
    consumer = re.search(
        r"^  -\s+username:\s*DASHBOARD\b(.*?)(?=^  -\s+username:|^###)",
        kong,
        re.DOTALL | re.MULTILINE,
    )
    assert consumer, "no DASHBOARD consumer in volumes/api/kong.yml"

    body = consumer.group(1)
    assert "basicauth_credentials:" in body, (
        "the DASHBOARD consumer declares no `basicauth_credentials`, so Kong's "
        "basic-auth plugin has nothing to match and rejects every request"
    )
    assert "$DASHBOARD_USERNAME" in body and "$DASHBOARD_PASSWORD" in body, (
        "credentials must reference $DASHBOARD_USERNAME/$DASHBOARD_PASSWORD — Kong's "
        "entrypoint expands them from the environment, so hardcoding would commit a secret"
    )


def test_dashboard_credential_is_declared_exactly_once():
    """Kong accepts credentials nested under a consumer *or* as a top-level
    `basicauth_credentials:` list keyed by `consumer:`. Declaring both is valid
    YAML and boots without complaint — Kong silently keeps one and discards the
    other, so a password change to the losing block is a no-op that looks applied.
    """
    kong = KONG_CONFIG.read_text(encoding="utf-8")

    top_level = re.findall(r"^basicauth_credentials:", kong, re.MULTILINE)
    assert not top_level, (
        "a top-level `basicauth_credentials:` block duplicates the credential nested "
        "under the DASHBOARD consumer; Kong keeps only one, so edits to the other "
        "silently do nothing"
    )

    declarations = re.findall(r"basicauth_credentials:", kong)
    assert len(declarations) == 1, (
        f"expected exactly one `basicauth_credentials:` declaration, found {len(declarations)}"
    )


def test_dashboard_service_still_has_the_basic_auth_plugin():
    """The credential is inert unless the `dashboard` service actually enables
    the plugin — losing the plugin would leave Studio open with the credential
    still present and the config still looking correct.
    """
    kong = KONG_CONFIG.read_text(encoding="utf-8")
    service = re.search(
        r"^  - name:\s*dashboard\b(.*?)(?=^  - name:|^###|\Z)",
        kong,
        re.DOTALL | re.MULTILINE,
    )
    assert service, "no `dashboard` service in volumes/api/kong.yml"
    assert "basic-auth" in service.group(1), (
        "the `dashboard` service no longer enables the `basic-auth` plugin, so the "
        "DASHBOARD credential gates nothing"
    )
