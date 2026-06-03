/**
 * JWT fixture helper for runtime-config tests.
 *
 * Builds anon-key JWTs with crafted `iss`, `ref`, and `sub` claims so the
 * `decodeAnonKeyProject` decoder + the `/api/config` route's
 * project-match fence can be exercised against base64url-encoded
 * payloads. NO real Supabase anon keys are ever checked into the repo.
 *
 * Spec: openspec/changes/add-ghcr-image-publishing/specs/frontend-runtime-config/spec.md
 *   - Requirement: Cross-Environment Configuration Fence — Anon-Key Project Match
 *     (specifically the "base64url-encoded JWT is decoded correctly" scenario)
 *
 * This file lives under `__fixtures__/` so the workspace vitest config
 * excludes it from auto-discovery. It is imported explicitly by the
 * test files that need it (e.g. `public-config.test.ts`,
 * `jwt-fixture.test.ts`, `route.test.ts`).
 *
 * Owned by openspec/changes/add-ghcr-image-publishing tasks.md §2.5 / §2.6.
 */

export type AnonKeyFixtureClaims = {
  iss?: string;
  ref?: string;
  sub?: string;
  /** Optional extra claims for callers that need them. */
  [key: string]: unknown;
};

/**
 * Base64url-encode a string (RFC 4648 §5): standard base64, then
 * substitute `+` → `-`, `/` → `_`, and strip padding `=` characters.
 *
 * Self-hosted Supabase JWTs use this encoding for both header and
 * payload, so fixtures must too — otherwise the decoder under test
 * never exercises the `-`/`_` substitution path.
 */
function base64UrlEncode(input: string): string {
  const b64 =
    typeof btoa === "function"
      ? btoa(input)
      : Buffer.from(input, "binary").toString("base64");
  return b64.replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

/**
 * Build a fake anon-key JWT carrying the requested claims.
 *
 * The signature segment is a fixed placeholder — the decoder under test
 * does NOT verify it. The header uses `{"alg":"HS256","typ":"JWT"}` to
 * mirror the real Supabase shape. Any claims listed in `claims` are
 * merged into the payload verbatim; pass `iss`, `ref`, `sub`, plus any
 * additional fields a specific test needs.
 *
 * @example
 *   const key = makeAnonKey({ iss: "https://bloom-dev.salk.edu", ref: "bloomdev" });
 *   const decoded = decodeAnonKeyProject(key);
 *   expect(decoded.iss).toBe("https://bloom-dev.salk.edu");
 */
export function makeAnonKey(claims: AnonKeyFixtureClaims = {}): string {
  const header = base64UrlEncode(JSON.stringify({ alg: "HS256", typ: "JWT" }));
  const payload = base64UrlEncode(JSON.stringify(claims));
  const signature = base64UrlEncode("fixture-signature-not-verified");
  return `${header}.${payload}.${signature}`;
}
