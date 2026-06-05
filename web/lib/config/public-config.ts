/**
 * Runtime public-config module.
 *
 * Spec: openspec/changes/add-ghcr-image-publishing/specs/frontend-runtime-config/spec.md
 *   - Requirement: Typed Public Config Module
 *   - Requirement: Cross-Environment Configuration Fence — Anon-Key Project Match
 *
 * Reads the eight `NEXT_PUBLIC_*` envs that `bloom-web` consumes at
 * request time (NOT at module load) so the same image runs in any
 * environment. After the PR-3 cutover, `web/Dockerfile.bloom-web.prod`
 * no longer bakes any of these into the JS bundle.
 *
 * USAGE
 *   - Server-side (route handlers, middleware, server components):
 *       const config = getPublicConfig();
 *       const url = config.supabaseUrl;
 *   - Client-side (`'use client'` components):
 *       const config = usePublicConfig(); // from ./use-public-config
 *
 * INVARIANTS
 *   - `getPublicConfig()` MUST be called per-request server-side, never at
 *     module load — that would re-introduce the same staleness this
 *     refactor exists to eliminate. The `web/tests/.../no-direct-next-public-reads`
 *     scanner test (added in PR-2 §4) fences this for browser-bound code.
 *   - The returned object's keys are EXACTLY the keys declared by the
 *     `PublicConfig` type. Adding a 9th field (e.g. `imageSha` from the
 *     deferred §12.6 follow-up) requires editing this file, the
 *     `/api/config` route handler, and a Vitest fixture together — the
 *     route handler's "exact keys" assertion is the structural choke point.
 *
 * THIS IS NOT AUTHENTICATION. `decodeAnonKeyProject()` does NOT verify
 * the JWT signature; it only reads the project-identifier claim so the
 * cross-environment fence can sanity-check key↔URL pairings. Forged
 * keys are RLS's job to refuse, not this module's.
 */

/** Public configuration consumed by bloom-web at runtime. */
export type PublicConfig = {
  /** Public Supabase URL (e.g. `https://api.bloom-staging.salkhpi.org`). */
  supabaseUrl: string | undefined;
  /** Supabase anonymous key (JWT). Public — no signing key access. */
  supabaseAnonKey: string | undefined;
  /** Auth-cookie name (must diverge between staging and prod). */
  supabaseCookieName: string | undefined;
  /** Bloom MCP server URL. */
  mcpUrl: string | undefined;
  /** Public app URL (used by oauth callback flows). */
  appUrl: string | undefined;
  /** Short git SHA exposed by `/api/health` as the liveness fingerprint. */
  commitSha: string | undefined;
  /** Public Storage URL for `.bin` fetches in the scRNA viewer. */
  storageUrl: string | undefined;
  /** Public Bloom URL (used by `/app/test` smoke page). */
  bloomUrl: string | undefined;
};

/**
 * Read the public config from `process.env` at call time.
 *
 * MUST be called per-request, never cached at module level. The whole point
 * of this module is that the same image binary serves different envs; if
 * the result is captured at module load it defeats the purpose.
 */
export function getPublicConfig(): PublicConfig {
  return {
    supabaseUrl: process.env.NEXT_PUBLIC_SUPABASE_URL,
    supabaseAnonKey: process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY,
    supabaseCookieName: process.env.NEXT_PUBLIC_SUPABASE_COOKIE_NAME,
    mcpUrl: process.env.NEXT_PUBLIC_MCP_URL,
    appUrl: process.env.NEXT_PUBLIC_APP_URL,
    commitSha: process.env.NEXT_PUBLIC_COMMIT_SHA,
    storageUrl: process.env.NEXT_PUBLIC_STORAGE_URL,
    bloomUrl: process.env.NEXT_PUBLIC_BLOOM_URL,
  };
}

// ─── Anon-key JWT decoder (Decision 14) ─────────────────────────────────────

/** Project-identifier claims extracted from an anon-key JWT. */
export type AnonKeyClaims = {
  /** Supabase Cloud convention: short project ref (e.g. `bloomdev`). */
  ref?: string;
  /** Self-hosted fallback: issuer URL. */
  iss?: string;
};

/**
 * Decode the `iss`/`ref` claims from an anonymous-key JWT.
 *
 * NON-CRYPTOGRAPHIC SANITY CHECK ONLY. The signature is NOT verified; we
 * don't have access to the signing key at the bloom-web layer. The decode
 * exists so `/api/config` can refuse to serve when the configured
 * `NEXT_PUBLIC_SUPABASE_ANON_KEY` clearly names a different project than
 * `NEXT_PUBLIC_SUPABASE_URL` — that's the kind of misconfiguration that
 * would silently route researcher writes to the wrong Supabase instance
 * if not caught. Forged keys are RLS's job.
 *
 * Handles base64url: substitutes `-` → `+`, `_` → `/`, and pads with `=`
 * to a multiple of 4 before `atob`. Self-hosted JWTs commonly contain
 * `-`/`_` in encoded segments (URL-safe), so the substitution is required.
 *
 * @throws when the input is not a 3-segment JWT or the payload fails to
 *   decode/parse. Callers MUST surface the throw as a 503 with a clear
 *   "anon-key is not a valid JWT" cause, per the spec.
 */
export function decodeAnonKeyProject(anonKey: string): AnonKeyClaims {
  const segments = anonKey.split(".");
  if (segments.length !== 3) {
    throw new Error(
      `anon-key is not a valid JWT (expected 3 segments, got ${segments.length})`,
    );
  }
  const payload = segments[1];
  // base64url → base64 substitution + padding to length % 4 === 0.
  const padded = payload.replace(/-/g, "+").replace(/_/g, "/");
  const padding = padded.length % 4 === 0 ? "" : "=".repeat(4 - (padded.length % 4));
  // JWTs are defined to carry UTF-8 JSON. Decode bytes → UTF-8 string,
  // NOT to binary (Latin-1). The naive `atob(...).toString('binary')`
  // path silently corrupts multi-byte unicode in claim values
  // (CJK ideographs, emoji, combining marks). Prefer Buffer when
  // available (Next.js nodejs runtime, all our tests); fall back to
  // atob + TextDecoder for client/Edge contexts that hypothetically
  // import this helper.
  let decoded: string;
  if (typeof Buffer !== "undefined") {
    decoded = Buffer.from(padded + padding, "base64").toString("utf-8");
  } else {
    const binary = atob(padded + padding);
    const bytes = Uint8Array.from(binary, (c) => c.charCodeAt(0));
    decoded = new TextDecoder("utf-8").decode(bytes);
  }
  let parsed: unknown;
  try {
    parsed = JSON.parse(decoded);
  } catch {
    throw new Error("anon-key payload is not valid JSON");
  }
  if (typeof parsed !== "object" || parsed === null) {
    throw new Error("anon-key payload is not an object");
  }
  const claims = parsed as Record<string, unknown>;
  const result: AnonKeyClaims = {};
  if (typeof claims.ref === "string") {
    result.ref = claims.ref;
  }
  if (typeof claims.iss === "string") {
    result.iss = claims.iss;
  }
  return result;
}
