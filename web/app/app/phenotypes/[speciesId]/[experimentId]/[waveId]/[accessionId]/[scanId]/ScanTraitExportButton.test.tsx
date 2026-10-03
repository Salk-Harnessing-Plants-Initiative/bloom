// @vitest-environment jsdom
/** The scan page's Download traits button (tasks.md 11.5; spec "Trait download entry points"). */

import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const auth = vi.hoisted(() => ({ refreshSession: vi.fn(), getSession: vi.fn() }))
vi.mock('@/lib/supabase/client', () => ({ createClientSupabaseClient: () => ({ auth }) }))

import ScanTraitExportButton from './ScanTraitExportButton'

const JOB = '0f8fad5b-d9cb-469f-a165-70867728950e'
const fetchSpy = vi.fn()

function reply(status: number, body: unknown) {
  return {
    ok: status >= 200 && status < 300,
    status,
    headers: new Headers(),
    json: async () => body,
    blob: async () => ({}) as Blob,
  }
}

const tick = (ms = 0) => act(() => vi.advanceTimersByTimeAsync(ms))
async function settle() {
  for (let i = 0; i < 10; i++) await tick()
}

const urls = () =>
  fetchSpy.mock.calls.map(([u, init]) => ({ url: new URL(String(u), 'http://localhost'), init }))

beforeEach(() => {
  vi.useFakeTimers()
  auth.refreshSession.mockReset()
  auth.refreshSession.mockResolvedValue({ data: {}, error: null })
  auth.getSession.mockResolvedValue({
    data: { session: { expires_at: Date.now() / 1000 + 3600 } },
    error: null,
  })
  fetchSpy.mockReset()
  fetchSpy.mockImplementation((u: string, init?: RequestInit) => {
    const url = new URL(String(u), 'http://localhost')
    if (url.pathname.endsWith('/recipes')) {
      return Promise.resolve(
        reply(200, {
          n_selected: 1,
          rows: [
            {
              recipe_key: 'legacy:5',
              recipe_kind: 'legacy',
              recipe_key_version: null,
              definition: { source_id: 5, source_name: 'legacy-five' },
              n_scans: 1,
              newest_source_id: 5,
              is_default: true,
            },
          ],
        })
      )
    }
    if (init?.method === 'POST') return Promise.resolve(reply(202, { job_id: JOB }))
    return new Promise(() => {})
  })
  vi.stubGlobal('fetch', fetchSpy)
})

afterEach(() => {
  vi.useRealTimers()
  cleanup()
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

describe('ScanTraitExportButton', () => {
  it('opens the dialog for one scan, with no wave or age filter', async () => {
    render(<ScanTraitExportButton scanId={577} />)
    fireEvent.click(screen.getByRole('button', { name: 'Download traits' }))
    await settle()
    const [list] = urls()
    expect(list.url.pathname).toBe('/api/cyl/trait-export/recipes')
    expect(Object.fromEntries(list.url.searchParams)).toEqual({ scan: '577' })
    expect(screen.queryAllByRole('combobox')).toHaveLength(0)
  })

  it('starts the job for that scan', async () => {
    render(<ScanTraitExportButton scanId={577} />)
    fireEvent.click(screen.getByRole('button', { name: 'Download traits' }))
    await settle()
    fireEvent.click(screen.getByRole('button', { name: /^download$/i }))
    await settle()
    const post = urls().find((c) => c.init?.method === 'POST')!
    expect(Object.fromEntries(post.url.searchParams)).toEqual({
      scan: '577',
      recipe: 'legacy:5',
      chosen: 'default',
    })
  })
})
