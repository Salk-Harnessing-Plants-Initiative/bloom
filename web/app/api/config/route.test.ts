// @vitest-environment jsdom
/**
 * Unit tests for the runtime public-config endpoint.
 *
 * Spec: openspec/changes/add-ghcr-image-publishing/specs/frontend-runtime-config/spec.md
 *   - Requirement: Public Config Endpoint
 *   - Requirement: Cross-Environment Configuration Fence — URL Hostname Mapping
 *   - Requirement: Cross-Environment Configuration Fence — Anon-Key Project Match
 *
 * The jsdom directive at the top of this file overrides the workspace
 * default of `environment: 'node'` (set in vitest.config.ts). Needed
 * because this test exercises `Response` objects + URL parsing — Node 18+
 * has both globally, but jsdom gives us a more browser-faithful surface
 * matching how the route handler will be exercised in production.
 *
 * STRUCTURAL CHOKE POINT
 *   The "response body keys match PublicConfig declared keys" assertion
 *   is the spec's intentional design pressure: adding a 9th PublicConfig
 *   key (e.g. `imageSha` from the deferred §12.6 follow-up) requires
 *   editing the type, the route handler's return, and the test fixture
 *   below — or this test fails. That's the desired co-edit gate.
 */

import { describe, expect, it } from "vitest";

import * as routeModule from "@/app/api/config/route";
import { makeAnonKey } from "@/lib/config/__fixtures__/jwt";

// ─── Helpers ────────────────────────────────────────────────────────────────

const PUBLIC_CONFIG_KEYS = [
  "appUrl",
  "bloomUrl",
  "commitSha",
  "mcpUrl",
  "storageUrl",
  "supabaseAnonKey",
  "supabaseCookieName",
  "supabaseUrl",
] as const;

function setHappyPathEnv(): void {
  process.env.NODE_ENV = "production";
  process.env.SUPABASE_URL = "http://kong:8000";
  process.env.SUPABASE_URL_HOSTS_ALLOWED = "kong:8000=bloom-dev.salk.edu";
  process.env.NEXT_PUBLIC_SUPABASE_URL = "https://bloom-dev.salk.edu/api";
  process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY = makeAnonKey({
    iss: "https://bloom-dev.salk.edu",
    ref: "bloomdev",
  });
  process.env.NEXT_PUBLIC_SUPABASE_COOKIE_NAME = "sb-bloom-auth-token";
  process.env.NEXT_PUBLIC_MCP_URL = "http://mcp.test";
  process.env.NEXT_PUBLIC_APP_URL = "https://app.test";
  process.env.NEXT_PUBLIC_COMMIT_SHA = "abc1234";
  process.env.NEXT_PUBLIC_STORAGE_URL = "https://storage.test";
  process.env.NEXT_PUBLIC_BLOOM_URL = "https://bloom.test";
}

async function call(): Promise<Response> {
  // Next.js App Router GET handlers take a Request and return Response.
  // The handler doesn't currently read from request, but we pass a
  // realistic Request anyway so any future request-shape change is
  // observable here.
  const request = new Request("https://app.test/api/config", {
    method: "GET",
  });
  return routeModule.GET(request);
}

async function jsonBody(response: Response): Promise<Record<string, unknown>> {
  const text = await response.text();
  return JSON.parse(text) as Record<string, unknown>;
}

// ─── Module-level exports ───────────────────────────────────────────────────

describe("/api/config — module exports", () => {
  it("exports dynamic = 'force-dynamic'", () => {
    expect(routeModule.dynamic).toBe("force-dynamic");
  });

  it("exports revalidate = 0", () => {
    expect(routeModule.revalidate).toBe(0);
  });

  it("exports runtime = 'nodejs'", () => {
    expect(routeModule.runtime).toBe("nodejs");
  });
});

// ─── Happy path ─────────────────────────────────────────────────────────────

describe("/api/config — happy path", () => {
  it("returns 200 with PublicConfig JSON when env is well-formed", async () => {
    setHappyPathEnv();
    const response = await call();
    expect(response.status).toBe(200);
    const body = await jsonBody(response);
    expect(body.supabaseUrl).toBe("https://bloom-dev.salk.edu/api");
    expect(body.supabaseCookieName).toBe("sb-bloom-auth-token");
  });

  it("response body keys exactly match the PublicConfig type's declared keys (structural choke point)", async () => {
    setHappyPathEnv();
    const response = await call();
    const body = await jsonBody(response);
    const keys = Object.keys(body).sort();
    expect(keys).toEqual([...PUBLIC_CONFIG_KEYS].sort());
  });

  it("sets Cache-Control: no-store, Vary: Host, Pragma: no-cache", async () => {
    setHappyPathEnv();
    const response = await call();
    expect(response.headers.get("Cache-Control")).toBe("no-store");
    expect(response.headers.get("Vary")).toBe("Host");
    expect(response.headers.get("Pragma")).toBe("no-cache");
  });

  it("returns Content-Type: application/json", async () => {
    setHappyPathEnv();
    const response = await call();
    const contentType = response.headers.get("Content-Type") ?? "";
    expect(contentType).toContain("application/json");
  });
});

// ─── URL-hostname fence (Decision 13) ───────────────────────────────────────

describe("/api/config — URL-hostname fence", () => {
  it("returns 503 when SUPABASE_URL_HOSTS_ALLOWED does not contain SUPABASE_URL's host", async () => {
    setHappyPathEnv();
    process.env.SUPABASE_URL_HOSTS_ALLOWED = "other:8000=bloom-dev.salk.edu";
    const response = await call();
    expect(response.status).toBe(503);
    const body = await jsonBody(response);
    expect(body.error).toMatch(/SUPABASE_URL host .* not declared/);
  });

  it("returns 503 when NEXT_PUBLIC_SUPABASE_URL's host is not in the allow-list for the internal host", async () => {
    setHappyPathEnv();
    process.env.NEXT_PUBLIC_SUPABASE_URL = "https://wrong-host.example/api";
    const response = await call();
    expect(response.status).toBe(503);
    const body = await jsonBody(response);
    expect(body.error).toMatch(
      /URL host .* not in SUPABASE_URL_HOSTS_ALLOWED/,
    );
  });

  it("returns 503 when SUPABASE_URL_HOSTS_ALLOWED is malformed", async () => {
    setHappyPathEnv();
    process.env.SUPABASE_URL_HOSTS_ALLOWED = "kong:8000-missing-equals";
    const response = await call();
    expect(response.status).toBe(503);
    const body = await jsonBody(response);
    expect(body.error).toMatch(/malformed/i);
  });
});

// ─── Anon-key fence (Decision 14) ───────────────────────────────────────────

describe("/api/config — anon-key project-match fence", () => {
  it("returns 200 when iss matches NEXT_PUBLIC_SUPABASE_URL host", async () => {
    setHappyPathEnv();
    // Re-derive with explicit iss only.
    process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY = makeAnonKey({
      iss: "https://bloom-dev.salk.edu",
    });
    const response = await call();
    expect(response.status).toBe(200);
  });

  it("returns 200 when ref matches the NEXT_PUBLIC_SUPABASE_URL hostname subdomain", async () => {
    setHappyPathEnv();
    // Self-hosted JWTs may not have iss; use ref instead. Spec scenario
    // says ref→subdomain check; for self-hosted matching the apex is OK.
    process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY = makeAnonKey({
      ref: "bloom-dev",
    });
    const response = await call();
    expect(response.status).toBe(200);
  });

  it("returns 503 when iss host does not match NEXT_PUBLIC_SUPABASE_URL host", async () => {
    setHappyPathEnv();
    process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY = makeAnonKey({
      iss: "https://prod-bloom.salk.edu",
      ref: "prodbloom",
    });
    const response = await call();
    expect(response.status).toBe(503);
    const body = await jsonBody(response);
    expect(body.error).toMatch(/anon-key project does not match URL/);
  });

  it("returns 503 when anon-key is not a valid JWT", async () => {
    setHappyPathEnv();
    process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY = "not-a-jwt";
    const response = await call();
    expect(response.status).toBe(503);
    const body = await jsonBody(response);
    expect(body.error).toMatch(/anon-key is not a valid JWT/);
  });

  it("returns 200 when JWT payload contains base64url characters that decode correctly", async () => {
    setHappyPathEnv();
    // Force payload to contain `-`/`_` via claim values that produce those
    // characters after substitution. The decoder MUST handle them.
    process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY = makeAnonKey({
      iss: "https://bloom-dev.salk.edu",
      ref: "ref-with-dashes_and_underscores",
      sub: "subject?with&special~chars",
    });
    const response = await call();
    expect(response.status).toBe(200);
  });
});

// ─── Missing required envs ──────────────────────────────────────────────────

describe("/api/config — missing required envs", () => {
  it("returns 503 when SUPABASE_URL is unset (server-internal)", async () => {
    setHappyPathEnv();
    delete process.env.SUPABASE_URL;
    const response = await call();
    expect(response.status).toBe(503);
    const body = await jsonBody(response);
    expect(body.error).toMatch(/SUPABASE_URL/);
  });

  it("returns 503 when SUPABASE_URL_HOSTS_ALLOWED is unset", async () => {
    setHappyPathEnv();
    delete process.env.SUPABASE_URL_HOSTS_ALLOWED;
    const response = await call();
    expect(response.status).toBe(503);
    const body = await jsonBody(response);
    expect(body.error).toMatch(/SUPABASE_URL_HOSTS_ALLOWED/);
  });
});
