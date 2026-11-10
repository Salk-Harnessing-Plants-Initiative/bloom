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
