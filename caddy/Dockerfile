# =============================================================================
# Bloom v2 — custom Caddy build with the Cloudflare DNS plugin baked in.
#
# Why this exists:
#   The stock caddy:*-alpine image ships the standard Caddy binary, which has
#   no DNS-provider modules linked in. Adding `tls { dns cloudflare ... }` to
#   the Caddyfile while running the stock image fails at startup with
#       unknown DNS provider module: cloudflare
#   because Caddy is a Go binary — its modules are statically linked at build
#   time, not loaded at runtime.
#
#   We need DNS-01 (rather than the default HTTP-01) because bloom-dev.salk.edu
#   sits behind Salk's firewall: Let's Encrypt cannot reach port 80 from the
#   public internet, so HTTP-01 cannot complete. CNAME-delegation pushes the
#   ACME challenge TXT record to a Cloudflare zone we control
#   (bloom-acme.talmolab.org), which Caddy can write to via the Cloudflare API.
#
# Why a custom Dockerfile (not a community image like caddybuilds/caddy-cloudflare):
#   Caddy terminates TLS for the entire stack. We prefer to trust only the
#   upstream Caddy project for that surface — official base image + only the
#   well-maintained caddy-dns/cloudflare plugin, pinned to known digests.
#
# Version + digest pinning rationale:
#   caddy 2.11.3       latest stable patch release as of 2026-05. For the
#                      specific security fixes included in this and earlier
#                      2.11.x releases, see the upstream CHANGELOG:
#                      https://github.com/caddyserver/caddy/blob/master/CHANGELOG.md
#   cloudflare plugin  fd0213c5482b3dd2a1f14fbce832fcff8e1ac4f8 is the commit
#                      tagged v0.2.4 (2026-03), tested against Caddy 2.11.x.
#                      We pin to the commit SHA, not the tag — git tags are
#                      moveable; a SHA is not.
#
#   The FROM lines use @sha256:... digests, not the floating version tag, so
#   a rebuild months from now reproduces the exact same image even if the
#   upstream tag is re-pushed. To bump:
#     1. caddy base images:
#          curl -sL "https://hub.docker.com/v2/repositories/library/caddy/tags/<NEW_TAG>" \
#            | python3 -c "import json,sys;print(json.load(sys.stdin)['digest'])"
#     2. cloudflare plugin:
#          git ls-remote --tags https://github.com/caddy-dns/cloudflare.git refs/tags/<NEW_TAG>
# =============================================================================

# -----------------------------------------------------------------------------
# Stage 1 — build a Caddy binary with the Cloudflare DNS plugin compiled in.
#
# The official caddy:2.11.3-builder-alpine image already ships xcaddy paired
# with the matching Caddy version, so we don't pin xcaddy separately.
# -----------------------------------------------------------------------------
FROM caddy:2.11.3-builder-alpine@sha256:3eae6b351ecdb05da6d16e341261a457692d344a435764c5ece7a60cf03a23f3 AS builder
RUN xcaddy build \
    --with github.com/caddy-dns/cloudflare@fd0213c5482b3dd2a1f14fbce832fcff8e1ac4f8

# -----------------------------------------------------------------------------
# Stage 2 — runtime image.
#
# Same slim Alpine base as the stock caddy image; we just swap in our binary.
# Image size delta vs. stock: ~5 MB (the linked-in Cloudflare API client).
# -----------------------------------------------------------------------------
FROM caddy:2.11.3-alpine@sha256:86deaf5e3d3408a6ccec08fbb79989783dd26e206ae10bcf78a801dc8c9ab794
COPY --from=builder /usr/bin/caddy /usr/bin/caddy
