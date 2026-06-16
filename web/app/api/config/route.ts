/**
 * Runtime public-config endpoint.
 *
 * Spec: openspec/changes/add-ghcr-image-publishing/specs/frontend-runtime-config/spec.md
 *   - Requirement: Public Config Endpoint
 *   - Requirement: Cross-Environment Configuration Fence — URL Hostname Mapping
 *   - Requirement: Cross-Environment Configuration Fence — Anon-Key Project Match
 *
 * Serves the runtime-resolved `PublicConfig` to client-side code so the
 * `bloom-web` JS bundle stays env-agnostic. Two fences refuse to serve
 * when the deployed environment is misconfigured in a way that would
 * silently route researcher writes to the wrong Supabase instance:
 *
 *   1. URL-host fence — `NEXT_PUBLIC_SUPABASE_URL`'s host MUST be a
 *      declared public counterpart of `process.env.SUPABASE_URL`'s host
 *      per `SUPABASE_URL_HOSTS_ALLOWED` (Decision 13).
 *   2. Anon-key project-match fence — the JWT's `iss`/`ref` claim MUST
 *      name a project consistent with `NEXT_PUBLIC_SUPABASE_URL` (Decision
 *      14). Non-cryptographic sanity check; forged keys are RLS's job.
 *
 * On any fence failure: 503 with `{ "error": "<cause>" }`. On success:
 * 200 with the full `PublicConfig` JSON.
 *
 * NEVER CACHED. `Cache-Control: no-store`, `Vary: Host`, `Pragma: no-cache`.
 * Forced to be dynamic at build time (`dynamic = 'force-dynamic'`,
 * `revalidate = 0`) and pinned to the Node runtime
 * (`runtime = 'nodejs'`) so a future accidental Edge migration doesn't
 * lose the runtime-injected env vars.
 */

import {
  type PublicConfig,
  decodeAnonKeyProject,
  getPublicConfig,
} from "@/lib/config/public-config";
import {
  MalformedHostsAllowedError,
  parseHostsAllowed,
} from "@/lib/config/validate-on-boot";

export const dynamic = "force-dynamic";
export const revalidate = 0;
export const runtime = "nodejs";

const RESPONSE_HEADERS: HeadersInit = {
  "Content-Type": "application/json; charset=utf-8",
  "Cache-Control": "no-store",
  Vary: "Host",
  Pragma: "no-cache",
};

function fenceFailure(cause: string): Response {
  return new Response(JSON.stringify({ error: cause }), {
    status: 503,
    headers: RESPONSE_HEADERS,
  });
}

/**
 * Sort the response body's keys deterministically. Two reasons:
 *   1. The spec scenario "response body keys exactly match PublicConfig
 *      declared keys" is a structural choke point that requires a stable
 *      ordering for the test to grep against.
 *   2. Stable output makes `/api/config` diffs in incident response
 *      readable (no key-order churn between deploys).
 */
function publicConfigToBody(config: PublicConfig): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  for (const key of Object.keys(config).sort()) {
    out[key] = (config as unknown as Record<string, unknown>)[key];
  }
  return out;
}

export function GET(_request: Request): Response {
  const config = getPublicConfig();

  // ─── Dev-mode early-return (mirrors validateOnBoot's Decision 13 exit) ───
  // In any non-production NODE_ENV the route MUST skip the URL and
  // anon-key fences and return whatever's configured. This preserves the
  // historic local-dev fallback at web/middleware.ts:9 — where
  // SUPABASE_URL may be unset and code falls back to
  // NEXT_PUBLIC_SUPABASE_URL — and prevents PR-2's `usePublicConfig()`
  // hook from breaking local dev the moment client components start
  // hitting /api/config (typical dev setups don't set
  // SUPABASE_URL_HOSTS_ALLOWED, and the dev anon key may not have an
  // iss/ref that matches a public URL).
  //
  // RLS remains the security boundary; this route handler's fences are
  // operator-misconfiguration sanity checks, not authentication.
  if (process.env.NODE_ENV !== "production") {
    return new Response(JSON.stringify(publicConfigToBody(config)), {
      status: 200,
      headers: RESPONSE_HEADERS,
    });
  }

  if (!config.supabaseUrl) {
    return fenceFailure(
      "Missing NEXT_PUBLIC_SUPABASE_URL — public Supabase URL is required",
    );
  }
  if (!config.supabaseAnonKey) {
    return fenceFailure(
      "Missing NEXT_PUBLIC_SUPABASE_ANON_KEY — anon key is required",
    );
  }

  // ─── Fence 1: URL hostname mapping (Decision 13) ─────────────────────────
  const internalUrl = process.env.SUPABASE_URL;
  if (!internalUrl) {
    return fenceFailure(
      "Missing SUPABASE_URL (server-internal) — required to validate the URL-host fence",
    );
  }
  const hostsAllowedRaw = process.env.SUPABASE_URL_HOSTS_ALLOWED;
  if (!hostsAllowedRaw) {
    return fenceFailure(
      "Missing SUPABASE_URL_HOSTS_ALLOWED — required to declare the " +
        "internal→public host mapping (see openspec/.../design.md " +
        "Decision 13 for format)",
    );
  }
  let mapping: Map<string, Set<string>>;
  try {
    mapping = parseHostsAllowed(hostsAllowedRaw);
  } catch (err) {
    if (err instanceof MalformedHostsAllowedError) {
      return fenceFailure(err.message);
    }
    throw err;
  }
  let internalHost: string;
  try {
    internalHost = new URL(internalUrl).host;
  } catch {
    return fenceFailure(`SUPABASE_URL is not a valid URL: ${internalUrl}`);
  }
  const allowedPublicHosts = mapping.get(internalHost);
  if (!allowedPublicHosts) {
    return fenceFailure(
      `SUPABASE_URL host '${internalHost}' not declared in ` +
        `SUPABASE_URL_HOSTS_ALLOWED. Allow-list keys: ` +
        `[${Array.from(mapping.keys()).join(", ")}]`,
    );
  }
  let publicHost: string;
  try {
    publicHost = new URL(config.supabaseUrl).host;
  } catch {
    return fenceFailure(
      `NEXT_PUBLIC_SUPABASE_URL is not a valid URL: ${config.supabaseUrl}`,
    );
  }
  if (!allowedPublicHosts.has(publicHost)) {
    return fenceFailure(
      `NEXT_PUBLIC_SUPABASE_URL host '${publicHost}' not in ` +
        `SUPABASE_URL_HOSTS_ALLOWED for internal host '${internalHost}'. ` +
        `Allowed: [${Array.from(allowedPublicHosts).join(", ")}]`,
    );
  }

  // ─── Fence 2: Anon-key project match (Decision 14) ───────────────────────
  let claims: { iss?: string; ref?: string };
  try {
    claims = decodeAnonKeyProject(config.supabaseAnonKey);
  } catch (err) {
    return fenceFailure(
      `anon-key is not a valid JWT: ${(err as Error).message}`,
    );
  }
  const projectMatches = matchesProject({
    publicHost,
    issClaim: claims.iss,
    refClaim: claims.ref,
  });
  if (!projectMatches) {
    return fenceFailure(
      `anon-key project does not match URL — JWT claims ` +
        `(iss=${claims.iss ?? "<unset>"}, ref=${claims.ref ?? "<unset>"}) ` +
        `do not name the deployed Supabase project at '${publicHost}'`,
    );
  }

  return new Response(JSON.stringify(publicConfigToBody(config)), {
    status: 200,
    headers: RESPONSE_HEADERS,
  });
}

/**
 * Decide whether the anon-key JWT names the same Supabase project as the
 * public URL. We accept a match on either claim:
 *
 *   - `iss` (Supabase Cloud convention): JWT's issuer URL host equals
 *     the public URL host.
 *   - `ref` (Supabase Cloud convention): the `ref` value appears as the
 *     first label of the public URL host (e.g. `bloomdev.supabase.co`).
 *     For self-hosted deployments without a `ref`-style subdomain, the
 *     `ref` claim is allowed to match the apex hostname's first label.
 *
 * Either match is sufficient — self-hosted JWTs may set only one. If
 * neither claim is reliable (Open Question 3 in design.md), this check
 * effectively reduces to "JWT is parseable" — still useful, but weaker
 * than the spec promises. PR-3 implementation MUST verify this against a
 * real anon-key sample before §11 lands.
 */
function matchesProject(args: {
  publicHost: string;
  issClaim?: string;
  refClaim?: string;
}): boolean {
  const { publicHost, issClaim, refClaim } = args;
  if (issClaim) {
    try {
      const issHost = new URL(issClaim).host;
      if (issHost === publicHost) {
        return true;
      }
    } catch {
      // iss is not a valid URL — fall through to ref check.
    }
  }
  if (refClaim) {
    // First label of the public host (e.g. 'bloomdev' from 'bloomdev.supabase.co').
    const firstLabel = publicHost.split(".")[0];
    if (refClaim === firstLabel) {
      return true;
    }
  }
  return false;
}
