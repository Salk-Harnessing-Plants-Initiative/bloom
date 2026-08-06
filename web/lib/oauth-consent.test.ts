/**
 * Unit tests for the OAuth consent helpers. The network calls are exercised
 * against a stub `fetch` — the real round-trip is covered end to end against a
 * live Supabase Auth in the OAuth flow validation.
 */

import { describe, it, expect, vi, beforeEach } from 'vitest'
import {
  safeNextPath,
  describeScopes,
  fetchAuthorization,
  submitConsent,
  redirectHost,
} from './oauth-consent'

beforeEach(() => {
  process.env.SUPABASE_URL = 'http://kong:8000'
  process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY = 'anon-key'
})

const stub = (status: number, body: unknown) =>
  vi.fn().mockResolvedValue({
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
  } as unknown as Response)

describe('safeNextPath', () => {
  it('accepts a same-site absolute path', () => {
    expect(safeNextPath('/oauth/consent?authorization_id=abc')).toBe(
      '/oauth/consent?authorization_id=abc'
    )
  })

  it('rejects a protocol-relative URL', () => {
    expect(safeNextPath('//evil.example.com')).toBeNull()
  })

  it('rejects an absolute URL', () => {
    expect(safeNextPath('https://evil.example.com/x')).toBeNull()
  })

  it('rejects a backslash-obfuscated path', () => {
    expect(safeNextPath('/\\evil.example.com')).toBeNull()
    expect(safeNextPath('/\\\\evil.example.com')).toBeNull()
  })

  it('rejects a tab hidden between the two slashes of a protocol-relative URL', () => {
    // The bypass the //-prefix + backslash-only checks missed: a tab is
    // invisible to a plain string comparison, but the WHATWG URL parser
    // strips ASCII tab/CR/LF as its first step, so every browser normalizes
    // this to //evil.example.com before navigating.
    expect(safeNextPath('/\t/evil.example.com')).toBeNull()
  })

  it('rejects a newline or carriage return hidden the same way', () => {
    expect(safeNextPath('/\n/evil.example.com')).toBeNull()
    expect(safeNextPath('/\r/evil.example.com')).toBeNull()
    expect(safeNextPath('/\r\n/evil.example.com')).toBeNull()
  })

  it('rejects a javascript: URI', () => {
    expect(safeNextPath('javascript:alert(1)')).toBeNull()
  })

  it('rejects empty and missing values', () => {
    expect(safeNextPath('')).toBeNull()
    expect(safeNextPath(null)).toBeNull()
    expect(safeNextPath(undefined)).toBeNull()
  })

  it('returns the resolved path unchanged for an ordinary query string and hash', () => {
    expect(safeNextPath('/oauth/consent?authorization_id=abc#section')).toBe(
      '/oauth/consent?authorization_id=abc#section'
    )
  })
})

describe('describeScopes', () => {
  it('maps known scopes to readable text', () => {
    expect(describeScopes('openid email')).toEqual([
      'Confirm your identity',
      'See your email address',
    ])
  })

  it('passes through an unknown scope rather than dropping it', () => {
    expect(describeScopes('email something_new')).toEqual([
      'See your email address',
      'something_new',
    ])
  })

  it('tolerates extra whitespace and an empty scope string', () => {
    expect(describeScopes('  email   profile ')).toEqual([
      'See your email address',
      'See your basic profile',
    ])
    expect(describeScopes('')).toEqual([])
  })
})

describe('redirectHost', () => {
  it('extracts the host from a valid redirect_uri', () => {
    expect(redirectHost('https://claude.ai/api/mcp/auth_callback')).toBe('claude.ai')
    expect(redirectHost('http://localhost:3000/cb')).toBe('localhost:3000')
  })

  it('falls back to the raw value for a malformed redirect_uri', () => {
    // A malformed redirect_uri is itself worth surfacing, not hiding behind
    // a thrown exception.
    expect(redirectHost('not-a-url')).toBe('not-a-url')
  })
})

describe('fetchAuthorization', () => {
  it('returns the pending authorization', async () => {
    const payload = {
      authorization_id: 'abc',
      redirect_uri: 'http://localhost:3000/cb',
      client: { id: 'c1', name: 'Claude Code' },
      user: { id: 'u1', email: 'someone@salk.edu' },
      scope: 'email profile',
    }
    const f = stub(200, payload)
    await expect(fetchAuthorization('abc', 'tok', f)).resolves.toEqual(payload)
  })

  it('returns null for an unknown or expired authorization', async () => {
    const f = stub(404, { msg: 'authorization not found' })
    await expect(fetchAuthorization('gone', 'tok', f)).resolves.toBeNull()
  })

  it("sends the user's bearer token and the anon apikey", async () => {
    const f = stub(200, { scope: '' })
    await fetchAuthorization('abc', 'user-token', f)
    const [, init] = f.mock.calls[0]
    expect(init.headers.Authorization).toBe('Bearer user-token')
    expect(init.headers.apikey).toBe('anon-key')
  })

  it('returns null when a 200 body is missing the client', async () => {
    // A response the consent screen cannot render — previously this was cast
    // to the expected type and crashed at `authorization.client.name`.
    const f = stub(200, {
      authorization_id: 'abc',
      user: { id: 'u1', email: 'someone@salk.edu' },
      scope: 'email',
    })
    await expect(fetchAuthorization('abc', 'tok', f)).resolves.toBeNull()
  })

  it('returns null when a 200 body is missing the user', async () => {
    const f = stub(200, {
      authorization_id: 'abc',
      client: { id: 'c1', name: 'Claude Desktop' },
      scope: 'email',
    })
    await expect(fetchAuthorization('abc', 'tok', f)).resolves.toBeNull()
  })

  it('returns null when a 200 body is missing scope', async () => {
    // page.tsx passes authorization.scope unconditionally to describeScopes(),
    // which throws on undefined — this must not reach render.
    const f = stub(200, {
      authorization_id: 'abc',
      redirect_uri: 'http://localhost:3000/cb',
      client: { id: 'c1', name: 'Claude Desktop' },
      user: { id: 'u1', email: 'someone@salk.edu' },
    })
    await expect(fetchAuthorization('abc', 'tok', f)).resolves.toBeNull()
  })

  it('returns null when a 200 body is missing redirect_uri', async () => {
    const f = stub(200, {
      authorization_id: 'abc',
      client: { id: 'c1', name: 'Claude Desktop' },
      user: { id: 'u1', email: 'someone@salk.edu' },
      scope: 'email',
    })
    await expect(fetchAuthorization('abc', 'tok', f)).resolves.toBeNull()
  })

  it('returns null when a 200 body is missing client.id or user.id', async () => {
    const missingClientId = stub(200, {
      authorization_id: 'abc',
      redirect_uri: 'http://localhost:3000/cb',
      client: { name: 'Claude Desktop' },
      user: { id: 'u1', email: 'someone@salk.edu' },
      scope: 'email',
    })
    await expect(fetchAuthorization('abc', 'tok', missingClientId)).resolves.toBeNull()

    const missingUserId = stub(200, {
      authorization_id: 'abc',
      redirect_uri: 'http://localhost:3000/cb',
      client: { id: 'c1', name: 'Claude Desktop' },
      user: { email: 'someone@salk.edu' },
      scope: 'email',
    })
    await expect(fetchAuthorization('abc', 'tok', missingUserId)).resolves.toBeNull()
  })

  it('returns null for an error body served with a 200', async () => {
    const f = stub(200, { code: 404, error_code: 'oauth_authorization_not_found' })
    await expect(fetchAuthorization('abc', 'tok', f)).resolves.toBeNull()
  })

  it('returns null when the body is not JSON', async () => {
    const f = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => {
        throw new SyntaxError('Unexpected token')
      },
    } as unknown as Response)
    await expect(fetchAuthorization('abc', 'tok', f)).resolves.toBeNull()
  })

  it('url-encodes the authorization id', async () => {
    const f = stub(200, {})
    await fetchAuthorization('a/b', 'tok', f)
    expect(f.mock.calls[0][0]).toContain('/oauth/authorizations/a%2Fb')
  })
})

describe('submitConsent', () => {
  it('returns the redirect url on approval', async () => {
    const f = stub(200, { redirect_url: 'http://localhost:3000/cb?code=xyz' })
    await expect(submitConsent('abc', 'approve', 'tok', f)).resolves.toBe(
      'http://localhost:3000/cb?code=xyz'
    )
    expect(JSON.parse(f.mock.calls[0][1].body)).toEqual({ action: 'approve' })
  })

  it('sends deny through unchanged', async () => {
    const f = stub(200, { redirect_url: 'http://localhost:3000/cb?error=access_denied' })
    await submitConsent('abc', 'deny', 'tok', f)
    expect(JSON.parse(f.mock.calls[0][1].body)).toEqual({ action: 'deny' })
  })

  it('throws with the server message on failure', async () => {
    const f = stub(400, { msg: 'authorization request cannot be processed' })
    await expect(submitConsent('abc', 'approve', 'tok', f)).rejects.toThrow(
      'authorization request cannot be processed'
    )
  })

  it('throws when the response carries no redirect_url', async () => {
    const f = stub(200, {})
    await expect(submitConsent('abc', 'approve', 'tok', f)).rejects.toThrow(/no redirect_url/)
  })
})
