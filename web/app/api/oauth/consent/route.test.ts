/**
 * Unit tests for the OAuth consent submission route.
 *
 * `submitConsent` (the actual GoTrue round-trip) is mocked here — the real
 * call is exercised in the OAuth flow's end-to-end validation. This file
 * covers what the route itself does with a good or bad result from it.
 */

import { describe, it, expect, vi, beforeEach } from 'vitest'

vi.mock('@/lib/supabase/server', () => ({
  getSession: vi.fn(),
}))
vi.mock('@/lib/oauth-consent', async () => {
  const actual = await vi.importActual<typeof import('@/lib/oauth-consent')>('@/lib/oauth-consent')
  return { ...actual, submitConsent: vi.fn() }
})

import { getSession } from '@/lib/supabase/server'
import { submitConsent } from '@/lib/oauth-consent'
import { POST } from './route'

const mockGetSession = getSession as unknown as ReturnType<typeof vi.fn>
const mockSubmitConsent = submitConsent as unknown as ReturnType<typeof vi.fn>

function request(body: unknown) {
  return new Request('https://app.test/api/oauth/consent', {
    method: 'POST',
    body: JSON.stringify(body),
  })
}

beforeEach(() => {
  mockGetSession.mockReset()
  mockSubmitConsent.mockReset()
  mockGetSession.mockResolvedValue({ access_token: 'user-token' })
})

describe('POST /api/oauth/consent', () => {
  it('returns the redirect_url on success', async () => {
    mockSubmitConsent.mockResolvedValue('http://localhost:3000/cb?code=xyz')

    const res = await POST(request({ authorization_id: 'abc', action: 'approve' }))
    const body = await res.json()

    expect(res.status).toBe(200)
    expect(body).toEqual({ redirect_url: 'http://localhost:3000/cb?code=xyz' })
  })

  it('rejects a malformed body', async () => {
    const res = await POST(
      new Request('https://app.test/api/oauth/consent', {
        method: 'POST',
        body: 'not json',
      })
    )
    expect(res.status).toBe(400)
  })

  it('rejects a missing authorization_id or an invalid action', async () => {
    const missingId = await POST(request({ action: 'approve' }))
    expect(missingId.status).toBe(400)

    const badAction = await POST(request({ authorization_id: 'abc', action: 'delete' }))
    expect(badAction.status).toBe(400)
  })

  it('rejects when there is no session', async () => {
    mockGetSession.mockResolvedValue(null)
    const res = await POST(request({ authorization_id: 'abc', action: 'approve' }))
    expect(res.status).toBe(401)
  })

  it('returns a generic message, not the raw upstream error, on failure', async () => {
    // GoTrue's own text for e.g. an already-consumed or expired authorization
    // — a real caller-facing detail this route must not pass through raw,
    // per the friendly-error convention the page itself already uses for
    // page-load failures (expired/invalid authorization, expired session).
    mockSubmitConsent.mockRejectedValue(
      new Error('oauth_authorization_not_found: authorization has already been consumed')
    )

    const res = await POST(request({ authorization_id: 'abc', action: 'approve' }))
    const body = await res.json()

    expect(res.status).toBe(400)
    expect(body.error).toBe('Could not complete the request. Please try again.')
    expect(body.error).not.toContain('oauth_authorization_not_found')
  })

  it('handles a second, concurrent submission for the same authorization_id without crashing', async () => {
    // ConsentForm only disables its button client-side after the first
    // click — nothing stops two POSTs for the same authorization_id in
    // flight together (double-click, a retried request). GoTrue's own
    // idempotency there is unverified from this repo; what this route must
    // do regardless is never crash and always return a well-formed
    // response for each call, whatever GoTrue decides.
    mockSubmitConsent
      .mockResolvedValueOnce('http://localhost:3000/cb?code=xyz')
      .mockRejectedValueOnce(new Error('authorization already consumed'))

    const [first, second] = await Promise.all([
      POST(request({ authorization_id: 'abc', action: 'approve' })),
      POST(request({ authorization_id: 'abc', action: 'approve' })),
    ])

    expect(first.status).toBe(200)
    expect((await first.json()).redirect_url).toBe('http://localhost:3000/cb?code=xyz')

    expect(second.status).toBe(400)
    expect((await second.json()).error).toBe('Could not complete the request. Please try again.')
  })
})
