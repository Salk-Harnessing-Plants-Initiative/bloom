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
#   well-maintained caddy-dns/cloudflare plugin, pinned to known versions.
#
# Version pinning rationale:
#   CADDY_VERSION           2.11.3   — latest stable as of 2026-05; patches
#                                      CVE-2026-30851 (forward_auth privesc,
#                                      fixed in 2.11.2) and CVE-2026-27589
#                                      (admin API CSRF, fixed in 2.11.1).
#   CLOUDFLARE_PLUGIN_REF   v0.2.4   — latest stable of caddy-dns/cloudflare
#                                      (2026-03), tested against Caddy 2.11.x.
# =============================================================================

ARG CADDY_VERSION=2.11.3
ARG CLOUDFLARE_PLUGIN_REF=v0.2.4

# -----------------------------------------------------------------------------
# Stage 1 — build a Caddy binary with the Cloudflare DNS plugin compiled in.
#
# The official caddy:${CADDY_VERSION}-builder-alpine image already ships
# xcaddy paired with the matching Caddy version, so we don't pin xcaddy here.
# -----------------------------------------------------------------------------
FROM caddy:${CADDY_VERSION}-builder-alpine AS builder
ARG CLOUDFLARE_PLUGIN_REF
RUN xcaddy build \
    --with github.com/caddy-dns/cloudflare@${CLOUDFLARE_PLUGIN_REF}

# -----------------------------------------------------------------------------
# Stage 2 — runtime image.
#
# Same slim Alpine base as the stock caddy image; we just swap in our binary.
# Image size delta vs. stock: ~5 MB (the linked-in Cloudflare API client).
# -----------------------------------------------------------------------------
FROM caddy:${CADDY_VERSION}-alpine
COPY --from=builder /usr/bin/caddy /usr/bin/caddy
