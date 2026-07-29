/**
 * Unit tests for the boot-time runtime-config validator.
 *
 * Spec: openspec/changes/add-ghcr-image-publishing/specs/frontend-runtime-config/spec.md
 *   - Requirement: Cross-Environment Configuration Fence — URL Hostname Mapping
 *
 * Two surfaces are tested here:
 *   1. `validateOnBoot()` — the public boot guard wired via
 *      `web/instrumentation.ts`. MUST throw in production when required
 *      keys are missing, MUST be a no-op in dev so the existing local
 *      fallback at web/middleware.ts:9 isn't broken.
 *   2. `parseHostsAllowed()` — the format parser for the
 *      `SUPABASE_URL_HOSTS_ALLOWED` env var. MUST throw a named
 *      `MalformedHostsAllowedError` only for structural misshapes
 *      (missing `=`, empty hostnames, leading/trailing commas, empty
 *      pairs between commas). **Duplicate internal-host keys are
 *      ALWAYS additive** — repeated pairs accumulate into the host's
 *      value-Set so multi-domain deployments can declare multiple
 *      allowed public hosts under one internal host. See
 *      design.md Decision 13.
 *
 * `process.env` mutations between tests are isolated by `vitest.setup.ts`.
 */

import { describe, expect, it } from "vitest";

import {
  MalformedHostsAllowedError,
  parseHostsAllowed,
  validateOnBoot,
} from "@/lib/config/validate-on-boot";

// ─── parseHostsAllowed ──────────────────────────────────────────────────────

describe("parseHostsAllowed", () => {
  it("returns a Map keyed by internal host with Set-of-public-hosts values", () => {
    const map = parseHostsAllowed("kong:8000=bloom-dev.salk.edu");
    expect(map.get("kong:8000")).toEqual(new Set(["bloom-dev.salk.edu"]));
  });

  it("supports multiple comma-separated pairs", () => {
    const map = parseHostsAllowed(
      "kong:8000=bloom-dev.salk.edu,kong:8000=staging-bloom-dev.salk.edu:8443",
    );
    expect(map.get("kong:8000")).toEqual(
      new Set(["bloom-dev.salk.edu", "staging-bloom-dev.salk.edu:8443"]),
    );
  });

  it("supports distinct internal hosts mapping to distinct public hosts", () => {
    const map = parseHostsAllowed(
      "kong:8000=bloom-dev.salk.edu,kong:9000=other.example",
    );
    expect(map.get("kong:8000")).toEqual(new Set(["bloom-dev.salk.edu"]));
    expect(map.get("kong:9000")).toEqual(new Set(["other.example"]));
  });

  it("throws on a pair missing `=`", () => {
    expect(() => parseHostsAllowed("kong:8000")).toThrowError(
      MalformedHostsAllowedError,
    );
    try {
      parseHostsAllowed("kong:8000");
    } catch (err) {
      expect((err as Error).message).toMatch(/missing '='/);
      expect((err as Error).message).toMatch(/kong:8000/);
    }
  });

  it("throws on an empty internal host", () => {
    expect(() => parseHostsAllowed("=bloom-dev.salk.edu")).toThrowError(
      MalformedHostsAllowedError,
    );
  });

  it("throws on an empty public host", () => {
    expect(() => parseHostsAllowed("kong:8000=")).toThrowError(
      MalformedHostsAllowedError,
    );
  });

  it("throws on a trailing comma", () => {
    expect(() => parseHostsAllowed("kong:8000=bloom-dev.salk.edu,")).toThrowError(
      MalformedHostsAllowedError,
    );
  });

  it("throws on a leading comma", () => {
    expect(() => parseHostsAllowed(",kong:8000=bloom-dev.salk.edu")).toThrowError(
      MalformedHostsAllowedError,
    );
  });

  it("does not throw when the same internal host is repeated with additive value", () => {
    // Multiple pairs for the same internal host MUST be allowed (multi-domain
    // deployments). Only EXACT duplicates of an internal→public pair count
    // as benign; this assertion fences that "additive == OK".
    const map = parseHostsAllowed(
      "kong:8000=bloom-dev.salk.edu,kong:8000=other.example",
    );
    expect(map.get("kong:8000")).toEqual(
      new Set(["bloom-dev.salk.edu", "other.example"]),
    );
  });

  it("normalizes host case to lower-case so it matches URL(...).host", () => {
    // DNS hostnames are case-insensitive, and the WHATWG URL parser always
    // lower-cases `URL(...).host`. Storing the allow-list verbatim would make
    // an operator's `Kong:8000=Bloom.salk.edu` fail to match the lower-cased
    // runtime host — a confusing spurious 503 where the two hosts look
    // identical except for case. Normalize both sides on parse.
    const map = parseHostsAllowed("Kong:8000=Bloom.SALK.edu");
    expect(map.get("kong:8000")).toEqual(new Set(["bloom.salk.edu"]));
    expect(map.has("Kong:8000")).toBe(false);
  });
});

// ─── validateOnBoot ─────────────────────────────────────────────────────────

describe("validateOnBoot — production mode", () => {
  function setProdEnv(overrides: Record<string, string | undefined> = {}): void {
    process.env.NODE_ENV = "production";
    process.env.NEXT_PUBLIC_SUPABASE_URL = "https://bloom-dev.salk.edu/api";
    process.env.SUPABASE_URL = "http://kong:8000";
    process.env.SUPABASE_URL_HOSTS_ALLOWED = "kong:8000=bloom-dev.salk.edu";
    for (const [key, value] of Object.entries(overrides)) {
      if (value === undefined) {
        delete process.env[key];
      } else {
        process.env[key] = value;
      }
    }
  }

  it("does NOT throw when all required keys are present and well-formed", () => {
    setProdEnv();
    expect(() => validateOnBoot()).not.toThrow();
  });

  it("throws when NEXT_PUBLIC_SUPABASE_URL is unset", () => {
    setProdEnv({ NEXT_PUBLIC_SUPABASE_URL: undefined });
    expect(() => validateOnBoot()).toThrowError(/NEXT_PUBLIC_SUPABASE_URL/);
  });

  it("throws when SUPABASE_URL is unset", () => {
    setProdEnv({ SUPABASE_URL: undefined });
    expect(() => validateOnBoot()).toThrowError(/SUPABASE_URL/);
  });

  it("throws when SUPABASE_URL_HOSTS_ALLOWED is unset", () => {
    setProdEnv({ SUPABASE_URL_HOSTS_ALLOWED: undefined });
    expect(() => validateOnBoot()).toThrowError(/SUPABASE_URL_HOSTS_ALLOWED/);
  });

  it("throws when SUPABASE_URL's host is not a key in HOSTS_ALLOWED", () => {
    setProdEnv({
      SUPABASE_URL: "http://other-internal:1234",
      SUPABASE_URL_HOSTS_ALLOWED: "kong:8000=bloom-dev.salk.edu",
    });
    expect(() => validateOnBoot()).toThrowError(
      /SUPABASE_URL host .* not declared in SUPABASE_URL_HOSTS_ALLOWED/,
    );
  });

  it("throws when SUPABASE_URL_HOSTS_ALLOWED is malformed", () => {
    setProdEnv({ SUPABASE_URL_HOSTS_ALLOWED: "kong:8000-missing-equals" });
    expect(() => validateOnBoot()).toThrowError(MalformedHostsAllowedError);
  });
});

describe("validateOnBoot — dev mode early-exit", () => {
  it("does NOT throw when NODE_ENV is undefined", () => {
    delete process.env.NODE_ENV;
    delete process.env.SUPABASE_URL_HOSTS_ALLOWED;
    delete process.env.NEXT_PUBLIC_SUPABASE_URL;
    delete process.env.SUPABASE_URL;
    expect(() => validateOnBoot()).not.toThrow();
  });

  it("does NOT throw when NODE_ENV is 'development'", () => {
    process.env.NODE_ENV = "development";
    delete process.env.SUPABASE_URL_HOSTS_ALLOWED;
    delete process.env.NEXT_PUBLIC_SUPABASE_URL;
    delete process.env.SUPABASE_URL;
    expect(() => validateOnBoot()).not.toThrow();
  });

  it("does NOT throw when NODE_ENV is 'test'", () => {
    process.env.NODE_ENV = "test";
    delete process.env.SUPABASE_URL_HOSTS_ALLOWED;
    delete process.env.NEXT_PUBLIC_SUPABASE_URL;
    delete process.env.SUPABASE_URL;
    expect(() => validateOnBoot()).not.toThrow();
  });
});
