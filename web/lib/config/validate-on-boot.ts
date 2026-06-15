/**
 * Boot-time runtime-config validator for `bloom-web`.
 *
 * Spec: openspec/changes/add-ghcr-image-publishing/specs/frontend-runtime-config/spec.md
 *   - Requirement: Cross-Environment Configuration Fence — URL Hostname Mapping
 *
 * `validateOnBoot()` runs once at process start via
 * `web/instrumentation.ts`'s `register()` hook (Next.js 16+ standard
 * pattern — see https://nextjs.org/docs/app/api-reference/file-conventions/instrumentation).
 *
 * CONTRACT
 *   - In production (`NODE_ENV === 'production'`):
 *       1. Asserts `NEXT_PUBLIC_SUPABASE_URL`, `SUPABASE_URL`, and
 *          `SUPABASE_URL_HOSTS_ALLOWED` are all set.
 *       2. Parses `SUPABASE_URL_HOSTS_ALLOWED` per the format defined in
 *          design.md Decision 13. Parse failures throw a typed
 *          `MalformedHostsAllowedError` with a specific cause.
 *       3. Asserts the host of `SUPABASE_URL` appears as a key in the
 *          parsed mapping. (The matching public→host check happens
 *          per-request in the `/api/config` route handler.)
 *       4. Any failure throws — the container exits non-zero before
 *          serving any request.
 *   - In non-production modes (`NODE_ENV !== 'production'`): returns
 *     immediately. Preserves the existing local-dev fallback at
 *     `web/middleware.ts:9` and `web/lib/supabase/server.ts:16` where
 *     `SUPABASE_URL` may be unset and code falls back to
 *     `NEXT_PUBLIC_SUPABASE_URL`. Without this exit, dev would break.
 *
 * SECURITY NOTE
 *   The dev-mode early-exit means a dev process pointed at a real prod
 *   Supabase URL (e.g. an operator manually sets
 *   `NEXT_PUBLIC_SUPABASE_URL=https://api.bloom.salk.edu/api` for "quick
 *   local test") will silently BYPASS the fence. This pre-existing risk
 *   isn't introduced by this change, but is worth knowing — RLS remains
 *   the last line of defense.
 *
 * Owned by openspec/changes/add-ghcr-image-publishing tasks.md §2.3 / §2.4.
 */

import { getPublicConfig } from "@/lib/config/public-config";

/** Thrown when `SUPABASE_URL_HOSTS_ALLOWED` is structurally malformed. */
export class MalformedHostsAllowedError extends Error {
  constructor(message: string) {
    super(`SUPABASE_URL_HOSTS_ALLOWED is malformed: ${message}`);
    this.name = "MalformedHostsAllowedError";
  }
}

/**
 * Parse `SUPABASE_URL_HOSTS_ALLOWED` into a Map<internal_host, Set<public_host>>.
 *
 * FORMAT (per design.md Decision 13):
 *   <internal_host>=<public_host>[,<internal_host>=<public_host>]*
 *
 * One internal host MAY map to multiple public hosts (multi-domain
 * deployment) — they are accumulated into the host's value-Set.
 *
 * Hosts include port. No URL parsing happens here. Hostnames are
 * case-insensitive (DNS) and the WHATWG URL parser always lower-cases
 * `URL(...).host`, so both sides of each pair are lower-cased on parse —
 * an operator's `Kong:8000=Bloom.salk.edu` still matches the lower-cased
 * runtime host instead of throwing a confusing "not in allow-list" 503.
 *
 * @throws MalformedHostsAllowedError on missing `=`, empty hostnames,
 *   leading/trailing commas, or whitespace-only segments.
 */
export function parseHostsAllowed(raw: string): Map<string, Set<string>> {
  if (raw === "") {
    throw new MalformedHostsAllowedError("value is empty");
  }
  if (raw.startsWith(",")) {
    throw new MalformedHostsAllowedError("leading comma");
  }
  if (raw.endsWith(",")) {
    throw new MalformedHostsAllowedError("trailing comma");
  }
  const result = new Map<string, Set<string>>();
  const pairs = raw.split(",");
  for (const pair of pairs) {
    if (pair.trim() === "") {
      throw new MalformedHostsAllowedError("empty pair between commas");
    }
    const eqIdx = pair.indexOf("=");
    if (eqIdx === -1) {
      throw new MalformedHostsAllowedError(`pair "${pair}" missing '='`);
    }
    // Lower-case both sides: DNS hosts are case-insensitive and
    // URL(...).host is always lower-cased, so the allow-list must match.
    const internal = pair.slice(0, eqIdx).trim().toLowerCase();
    const publicHost = pair.slice(eqIdx + 1).trim().toLowerCase();
    if (internal === "") {
      throw new MalformedHostsAllowedError(`pair "${pair}" has empty internal host`);
    }
    if (publicHost === "") {
      throw new MalformedHostsAllowedError(`pair "${pair}" has empty public host`);
    }
    let bucket = result.get(internal);
    if (!bucket) {
      bucket = new Set<string>();
      result.set(internal, bucket);
    }
    bucket.add(publicHost);
  }
  return result;
}

/**
 * Validate runtime config at container boot.
 *
 * MUST be called from `web/instrumentation.ts`'s `register()` hook. The
 * Vitest test `web/instrumentation.test.ts` greps the source to enforce
 * that the wiring is present.
 *
 * In dev mode (NODE_ENV !== 'production'), returns immediately so the
 * existing local fallback at web/middleware.ts:9 keeps working. See the
 * security note in this file's header — it's a deliberate trade-off.
 *
 * @throws on missing required keys, malformed hosts-allowed value, or
 *   internal-host not declared in the allow-list. Caller (the
 *   instrumentation hook) MUST let the throw propagate so the process
 *   exits non-zero before serving requests.
 */
export function validateOnBoot(): void {
  // Dev-mode early-exit. Treat anything other than literal 'production'
  // as dev — covers NODE_ENV=development, NODE_ENV=test, and undefined.
  if (process.env.NODE_ENV !== "production") {
    return;
  }
  // Obtain the public URL via getPublicConfig() rather than reading
  // process.env.NEXT_PUBLIC_SUPABASE_URL directly, keeping every
  // NEXT_PUBLIC_* access centralized in public-config.ts (the future
  // "No Direct NEXT_PUBLIC Reads" invariant lands in PR-2 §4). Behavior is
  // identical — getPublicConfig() reads process.env at call time.
  const publicUrl = getPublicConfig().supabaseUrl;
  if (!publicUrl) {
    throw new Error(
      "Missing required env: NEXT_PUBLIC_SUPABASE_URL. Production builds " +
        "MUST set this — it's the public Supabase URL the browser will call.",
    );
  }
  const internalUrl = process.env.SUPABASE_URL;
  if (!internalUrl) {
    throw new Error(
      "Missing required env: SUPABASE_URL. Production builds MUST set this — " +
        "it's the server-internal Supabase URL (typically http://kong:8000 " +
        "in the docker network).",
    );
  }
  const hostsAllowedRaw = process.env.SUPABASE_URL_HOSTS_ALLOWED;
  if (!hostsAllowedRaw) {
    throw new Error(
      "Missing required env: SUPABASE_URL_HOSTS_ALLOWED. Production builds " +
        "MUST declare the internal→public hostname mapping that " +
        "/api/config will validate. See openspec/.../design.md Decision 13 " +
        'for format (e.g. "kong:8000=bloom-dev.salk.edu").',
    );
  }
  // Parser throws MalformedHostsAllowedError directly — let it propagate.
  const mapping = parseHostsAllowed(hostsAllowedRaw);
  let internalHost: string;
  try {
    internalHost = new URL(internalUrl).host;
  } catch {
    throw new Error(`SUPABASE_URL is not a valid URL: ${internalUrl}`);
  }
  if (!mapping.has(internalHost)) {
    throw new Error(
      `SUPABASE_URL host '${internalHost}' is not declared in ` +
        "SUPABASE_URL_HOSTS_ALLOWED. Either add a mapping pair for it or " +
        "correct SUPABASE_URL to match an existing key. Allow-list keys: " +
        `[${Array.from(mapping.keys()).join(", ")}]`,
    );
  }
}
