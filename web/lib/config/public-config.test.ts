/**
 * Unit tests for the runtime public-config module.
 *
 * Spec: openspec/changes/add-ghcr-image-publishing/specs/frontend-runtime-config/spec.md
 *   - Requirement: Typed Public Config Module
 *   - Requirement: Cross-Environment Configuration Fence — Anon-Key Project Match
 *     (base64url decode exercised here via decodeAnonKeyProject)
 *
 * These tests exercise the *deferred-read* semantics of getPublicConfig:
 *   - The function reads `process.env` at call time, not at module load.
 *     Mutating an env var between calls MUST be reflected in the next call.
 *   - The returned object's keys are exactly the 8 declared public-config keys.
 *
 * Plus the JWT decoder used by the cross-environment fence (Decision 14):
 *   - Round-trips through base64url substitution (`-` ↔ `+`, `_` ↔ `/`) plus
 *     missing padding so self-hosted JWTs with `-`/`_` characters decode
 *     correctly.
 */

import { describe, expect, it, expectTypeOf } from "vitest";

import {
  type PublicConfig,
  decodeAnonKeyProject,
  getPublicConfig,
} from "@/lib/config/public-config";
import { makeAnonKey } from "@/lib/config/__fixtures__/jwt";

// ─── PublicConfig shape ─────────────────────────────────────────────────────

const EXPECTED_KEYS: ReadonlyArray<keyof PublicConfig> = [
  "supabaseUrl",
  "supabaseAnonKey",
  "supabaseCookieName",
  "mcpUrl",
  "appUrl",
  "commitSha",
  "storageUrl",
  "bloomUrl",
];

describe("getPublicConfig", () => {
  it("returns a record with exactly the 8 declared keys", () => {
    process.env.NEXT_PUBLIC_SUPABASE_URL = "https://example.test";
    process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY = "anon-key";
    process.env.NEXT_PUBLIC_SUPABASE_COOKIE_NAME = "sb-test";
    process.env.NEXT_PUBLIC_MCP_URL = "http://mcp.test";
    process.env.NEXT_PUBLIC_APP_URL = "https://app.test";
    process.env.NEXT_PUBLIC_COMMIT_SHA = "abc1234";
    process.env.NEXT_PUBLIC_STORAGE_URL = "https://storage.test";
    process.env.NEXT_PUBLIC_BLOOM_URL = "https://bloom.test";

    const config = getPublicConfig();
    const keys = Object.keys(config).sort();
    expect(keys).toEqual([...EXPECTED_KEYS].sort());
  });

  it("reads process.env at call time, not at module load", () => {
    process.env.NEXT_PUBLIC_SUPABASE_URL = "https://first.example";
    const first = getPublicConfig();
    expect(first.supabaseUrl).toBe("https://first.example");

    // Mutate AFTER first call and verify the second call sees the change.
    process.env.NEXT_PUBLIC_SUPABASE_URL = "https://second.example";
    const second = getPublicConfig();
    expect(second.supabaseUrl).toBe("https://second.example");
  });

  it("returns the PublicConfig type (compile-time check)", () => {
    expectTypeOf(getPublicConfig).returns.toEqualTypeOf<PublicConfig>();
  });

  it("returns undefined for keys whose env vars are unset", () => {
    // Confirm the contract callers like web/lib/supabase/client.ts depend on:
    // missing env → undefined, so the caller can apply its own default.
    delete process.env.NEXT_PUBLIC_SUPABASE_COOKIE_NAME;
    const config = getPublicConfig();
    expect(config.supabaseCookieName).toBeUndefined();
  });
});

// ─── JWT project-ref decoder (Decision 14) ──────────────────────────────────

describe("decodeAnonKeyProject", () => {
  it("returns iss when present", () => {
    const jwt = makeAnonKey({ iss: "https://bloom-dev.salk.edu" });
    const claims = decodeAnonKeyProject(jwt);
    expect(claims.iss).toBe("https://bloom-dev.salk.edu");
  });

  it("returns ref when present", () => {
    const jwt = makeAnonKey({ ref: "bloomdev" });
    const claims = decodeAnonKeyProject(jwt);
    expect(claims.ref).toBe("bloomdev");
  });

  it("returns both iss and ref when both are present", () => {
    const jwt = makeAnonKey({
      iss: "https://bloom-dev.salk.edu",
      ref: "bloomdev",
    });
    const claims = decodeAnonKeyProject(jwt);
    expect(claims).toEqual({
      iss: "https://bloom-dev.salk.edu",
      ref: "bloomdev",
    });
  });

  it("handles base64url characters (-, _) in the payload", () => {
    // makeAnonKey emits base64url; this test exercises the substitution path.
    // Use an iss with characters that produce `-` or `_` in the encoded
    // segment to ensure decodeAnonKeyProject's atob doesn't choke.
    const jwt = makeAnonKey({
      iss: "https://bloom-dev.salk.edu",
      ref: "with-dashes_and_underscores",
      sub: "user~contains?special&chars",
    });
    const claims = decodeAnonKeyProject(jwt);
    expect(claims.iss).toBe("https://bloom-dev.salk.edu");
    expect(claims.ref).toBe("with-dashes_and_underscores");
  });

  it("throws on a non-JWT string", () => {
    expect(() => decodeAnonKeyProject("not-a-jwt")).toThrow();
  });

  it("throws on JWT with fewer than 3 segments", () => {
    expect(() => decodeAnonKeyProject("only.two")).toThrow();
  });
});
