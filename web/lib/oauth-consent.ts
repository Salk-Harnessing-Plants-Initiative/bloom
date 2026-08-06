/**
 * Helpers for the OAuth 2.1 authorization/consent screen.
 *
 * Supabase Auth redirects a user here mid-flow with an `authorization_id`. The
 * screen reads the pending authorization, shows who is asking for what, and
 * submits the user's decision. Auth requires the authorization to be fetched
 * before a decision is accepted, so `fetchAuthorization` is a required step of
 * the flow rather than a convenience for rendering.
 */

export type ConsentAction = 'approve' | 'deny'

export interface PendingAuthorization {
  authorization_id: string
  redirect_uri: string
  client: { id: string; name: string }
  user: { id: string; email: string }
  scope: string
}

/** Scopes Supabase issues today, mapped to what they actually disclose. */
const SCOPE_LABELS: Record<string, string> = {
  openid: 'Confirm your identity',
  email: 'See your email address',
  profile: 'See your basic profile',
  phone: 'See your phone number',
}

export function describeScopes(scope: string): string[] {
  return scope
    .split(/\s+/)
    .filter(Boolean)
    .map((s) => SCOPE_LABELS[s] ?? s)
}

/**
 * A post-login return path, or null when it can't be trusted. Only same-site
 * absolute paths are allowed — anything protocol-relative (`//evil.com`),
 * absolute-URL shaped, or that a URL parser would normalize into either of
 * those turns the login form into an open redirect.
 *
 * Validated by actually resolving `raw` with the same WHATWG URL parser every
 * browser uses, against a fixed placeholder origin, and checking the result
 * never left that origin — not a blocklist of individual characters. An
 * earlier version rejected a literal `//` prefix and literal backslashes,
 * but not a tab/CR/LF hidden between the two slashes (e.g. `/\t/evil.com`):
 * invisible to that plain string comparison, but the URL Standard strips
 * ASCII tab/CR/LF from a URL string as the very first parsing step, so every
 * browser normalizes it to `//evil.com` before navigating. Returns the
 * resolved path, not the raw input, so nothing downstream can reinterpret a
 * character this parser already normalized away.
 */
export function safeNextPath(raw: string | null | undefined): string | null {
  if (!raw) return null
  if (!raw.startsWith('/')) return null

  const PLACEHOLDER_ORIGIN = 'https://placeholder.invalid'
  let resolved: URL
  try {
    resolved = new URL(raw, PLACEHOLDER_ORIGIN)
  } catch {
    return null
  }
  if (resolved.origin !== PLACEHOLDER_ORIGIN) return null

  return `${resolved.pathname}${resolved.search}${resolved.hash}`
}

/**
 * The host a consent decision will redirect back to, for display on the
 * consent screen. Dynamic registration means `client.name` is self-asserted
 * by the registrant — a malicious MCP client can register itself as
 * "Claude Desktop" while pointing at an attacker-owned `redirect_uri`, and
 * without this the approving human has no way to notice the mismatch.
 * Returns the raw value when it isn't a parseable URL rather than throwing —
 * a malformed `redirect_uri` is itself worth surfacing, not hiding.
 */
export function redirectHost(redirectUri: string): string {
  try {
    return new URL(redirectUri).host
  } catch {
    return redirectUri
  }
}

function authBase(): string {
  const url = process.env.SUPABASE_URL || process.env.NEXT_PUBLIC_SUPABASE_URL
  if (!url) throw new Error('SUPABASE_URL is not configured')
  return `${url.replace(/\/$/, '')}/auth/v1`
}

function headers(accessToken: string): Record<string, string> {
  const anon = process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY
  if (!anon) throw new Error('NEXT_PUBLIC_SUPABASE_ANON_KEY is not configured')
  return {
    'Content-Type': 'application/json',
    apikey: anon,
    Authorization: `Bearer ${accessToken}`,
  }
}

/**
 * Is this a response we can actually render a consent screen from?
 *
 * Checked field-by-field against every field `PendingAuthorization` declares
 * — not just the ones the page happens to read today (`client.name`,
 * `user.email`) — because `page.tsx` also passes `authorization.scope`
 * unconditionally to `describeScopes()`, which throws on `undefined`. A
 * previous version of this check validated only `authorization_id`,
 * `client.name`, and `user.email`; a 200 response missing `scope`,
 * `redirect_uri`, or either id field passed it and crashed the page for a
 * real user mid-login instead of falling back to the "no longer valid"
 * screen `fetchAuthorization`'s callers already handle for every other
 * malformed-response shape.
 */
function isRenderable(value: unknown): value is PendingAuthorization {
  if (typeof value !== 'object' || value === null) return false
  const v = value as Record<string, unknown>
  const client = v.client as Record<string, unknown> | undefined
  const user = v.user as Record<string, unknown> | undefined
  return (
    typeof v.authorization_id === 'string' &&
    typeof v.redirect_uri === 'string' &&
    typeof client?.id === 'string' &&
    typeof client?.name === 'string' &&
    typeof user?.id === 'string' &&
    typeof user?.email === 'string' &&
    typeof v.scope === 'string'
  )
}

/** The pending authorization, or null when it is unknown, expired, or
 * came back in a shape the consent screen cannot render. */
export async function fetchAuthorization(
  authorizationId: string,
  accessToken: string,
  fetchImpl: typeof fetch = fetch
): Promise<PendingAuthorization | null> {
  const res = await fetchImpl(
    `${authBase()}/oauth/authorizations/${encodeURIComponent(authorizationId)}`,
    { headers: headers(accessToken), cache: 'no-store' }
  )
  if (!res.ok) return null
  const body = await res.json().catch(() => null)
  return isRenderable(body) ? body : null
}

/**
 * Submit the user's decision. Returns the URL to send them back to, which
 * carries the authorization code on approval and an error on denial.
 */
export async function submitConsent(
  authorizationId: string,
  action: ConsentAction,
  accessToken: string,
  fetchImpl: typeof fetch = fetch
): Promise<string> {
  const res = await fetchImpl(
    `${authBase()}/oauth/authorizations/${encodeURIComponent(authorizationId)}/consent`,
    { method: 'POST', headers: headers(accessToken), body: JSON.stringify({ action }) }
  )
  const body = await res.json().catch(() => null)
  if (!res.ok) {
    throw new Error(body?.msg || `consent failed with status ${res.status}`)
  }
  const url = body?.redirect_url
  if (typeof url !== 'string' || !url) {
    throw new Error('consent succeeded but returned no redirect_url')
  }
  return url
}
