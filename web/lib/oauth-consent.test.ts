/**
 * Unit tests for the OAuth consent helpers. The network calls are exercised
 * against a stub `fetch` — the real round-trip is covered end to end against a
 * live Supabase Auth in the OAuth flow validation.
 */

import { describe, it, expect, vi, beforeEach } from 'vitest'
import { safeNextPath, describeScopes, fetchAuthorization, submitConsent } from './oauth-consent'

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
  })

  it('rejects empty and missing values', () => {
    expect(safeNextPath('')).toBeNull()
    expect(safeNextPath(null)).toBeNull()
    expect(safeNextPath(undefined)).toBeNull()
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
