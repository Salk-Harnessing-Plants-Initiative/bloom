/**
 * Request checks shared by the trait-export routes (design D7): same-origin, verified
 * identity, and parameter parsing. Every refusal is a `{ detail }` JSON response.
 */

import { NextResponse } from 'next/server'

import { createServerSupabaseClient, getSession } from '@/lib/supabase/server'

import type { Selection } from './selection'

export type Identity = { userId: string; token: string; tokenExp: number }

export function detail(status: number, message: string, extra: object = {}): Response {
  return NextResponse.json({ detail: message, ...extra }, { status })
}

/**
 * Browsers send `Sec-Fetch-Site` on every fetch and page JavaScript cannot set it.
 * `Origin` is omitted on same-origin GETs, so it can't guard these routes. A browser
 * too old to send the header is let through, which is accepted.
 */
export function sameOrigin(request: Request): boolean {
  const site = request.headers.get('sec-fetch-site')
  return site === null || site === 'same-origin'
}

function claims(jwt: string): { sub?: unknown; exp?: unknown } | null {
  const part = jwt.split('.')[1]
  if (!part) return null
  try {
    return JSON.parse(Buffer.from(part, 'base64url').toString('utf8'))
  } catch {
    return null
  }
}

/**
 * The cookie session's access token, verified against GoTrue (`getSession` alone only
 * decodes the cookie). The same token string is decoded for `exp`, and its subject
 * must be the verified user. GoTrue rejecting the token is 401; GoTrue failing is 503.
 */
export async function verifyIdentity(): Promise<Identity | Response> {
  const session = await getSession()
  const token = session?.access_token
  if (!token) return detail(401, 'Sign in to download traits.')
  const supabase = await createServerSupabaseClient()
  let user: { id: string } | null = null
  try {
    const { data, error } = await supabase.auth.getUser(token)
    if (error) {
      const status = (error as { status?: number }).status
      if (status !== undefined && status >= 500) {
        return detail(503, 'sign-in service unavailable; try again shortly')
      }
      return detail(401, 'Sign in to download traits.')
    }
    user = data.user
  } catch {
    return detail(503, 'sign-in service unavailable; try again shortly')
  }
  const c = claims(token)
  if (!user || !c || c.sub !== user.id || typeof c.exp !== 'number') {
    return detail(401, 'Sign in to download traits.')
  }
  return { userId: user.id, token, tokenExp: c.exp }
}

const INTEGER = /^(0|[1-9][0-9]{0,14})$/
const RECIPE = /^([0-9a-f]{64}|legacy:[0-9]+|unattributed)$/

function one(params: URLSearchParams, name: string): string | undefined | null {
  const all = params.getAll(name)
  if (all.length > 1) return null
  return all[0]
}

function int(
  params: URLSearchParams,
  name: string,
  positive: boolean
): number | undefined | string {
  const raw = one(params, name)
  if (raw === null) return `${name} is given more than once`
  if (raw === undefined) return undefined
  if (!INTEGER.test(raw)) return `${name} must be a whole number`
  const n = Number(raw)
  if (positive && n === 0) return `${name} must be positive`
  return n
}

/** An experiment (optionally one wave and/or age), or one scan. A string is a 422. */
export function parseSelection(url: URL): Selection | string {
  const p = url.searchParams
  const experiment = int(p, 'experiment', true)
  const scan = int(p, 'scan', true)
  const wave = int(p, 'wave', false)
  const age = int(p, 'age', false)
  for (const v of [experiment, scan, wave, age]) if (typeof v === 'string') return v
  if ((experiment === undefined) === (scan === undefined)) {
    return 'give exactly one of experiment and scan'
  }
  if (scan !== undefined) {
    if (wave !== undefined || age !== undefined) return 'wave and age apply only to an experiment'
    return { scan: scan as number }
  }
  const sel: Selection = { experiment: experiment as number }
  if (wave !== undefined) sel.wave = wave as number
  if (age !== undefined) sel.age = age as number
  return sel
}

export function parseJobRequest(
  url: URL
): { selection: Selection; recipe: string; chosen: 'default' | 'user' } | string {
  const selection = parseSelection(url)
  if (typeof selection === 'string') return selection
  const recipe = one(url.searchParams, 'recipe')
  if (recipe === null || recipe === undefined || !RECIPE.test(recipe)) {
    return 'recipe must be a recipe key'
  }
  const chosen = one(url.searchParams, 'chosen')
  if (chosen !== 'default' && chosen !== 'user') return 'chosen must be default or user'
  return { selection, recipe, chosen }
}
