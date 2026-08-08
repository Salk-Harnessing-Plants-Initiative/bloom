/**
 * Records the user's decision on a pending OAuth authorization.
 *
 * Runs server-side so the caller's Supabase access token is read from the
 * session cookie and never handed to the browser. Supabase ties the resulting
 * consent to whichever user that token belongs to, so a caller can only ever
 * consent as themselves.
 */

import { NextResponse } from 'next/server'
import { getSession } from '@/lib/supabase/server'
import { submitConsent, type ConsentAction } from '@/lib/oauth-consent'

const ACTIONS: ConsentAction[] = ['approve', 'deny']

export async function POST(request: Request) {
  let body: { authorization_id?: string; action?: string }
  try {
    body = await request.json()
  } catch {
    return NextResponse.json({ error: 'Malformed request body' }, { status: 400 })
  }

  const authorizationId = body.authorization_id
  const action = body.action as ConsentAction | undefined

  if (!authorizationId || !action || !ACTIONS.includes(action)) {
    return NextResponse.json(
      { error: "authorization_id and an action of 'approve' or 'deny' are required" },
      { status: 400 }
    )
  }

  const session = await getSession()
  if (!session?.access_token) {
    return NextResponse.json({ error: 'Not signed in' }, { status: 401 })
  }

  try {
    const redirectUrl = await submitConsent(authorizationId, action, session.access_token)
    return NextResponse.json({ redirect_url: redirectUrl })
  } catch (err) {
    // Logged, not shown: `err.message` here is GoTrue's own raw response text
    // (submitConsent throws `body.msg` verbatim) — inconsistent with the
    // friendly, translated messages ConsentPage already shows for every
    // page-load failure (expired/invalid authorization, expired session).
    // The client sees a stable, generic message; whoever's debugging a
    // real failure has the real one in the server log.
    console.error('oauth consent submission failed:', err)
    return NextResponse.json(
      { error: 'Could not complete the request. Please try again.' },
      { status: 400 }
    )
  }
}
