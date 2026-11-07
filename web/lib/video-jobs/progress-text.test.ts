import { describe, expect, it } from 'vitest'
import type { VideoJob } from '@/lib/video-job'
import { finishedNote, queuedText, renderingText, requestedText } from './progress-text'

const job = (over: Partial<VideoJob> = {}): VideoJob => ({
  id: 'j',
  status: 'rendering',
  stage: 'downloading',
  frames_done: 0,
  frames_total: 0,
  error_code: null,
  queued_ahead: null,
  created_at: '2026-10-29T10:00:00Z',
  updated_at: '2026-10-29T10:00:00Z',
  ...over,
})

describe('queuedText', () => {
  it('says next up with nothing ahead', () => {
    expect(queuedText(0)).toBe('Queued (next up)')
    expect(queuedText(null)).toBe('Queued (next up)')
  })

  it('gives the place in line', () => {
    expect(queuedText(1)).toBe('Queued (2nd in line)')
    expect(queuedText(2)).toBe('Queued (3rd in line)')
    expect(queuedText(3)).toBe('Queued (4th in line)')
    expect(queuedText(10)).toBe('Queued (11th in line)')
    expect(queuedText(20)).toBe('Queued (21st in line)')
  })
})

describe('renderingText', () => {
  it('gives a percentage while frames are counted', () => {
    expect(renderingText(job({ frames_done: 45, frames_total: 100 }))).toBe('Rendering 45%')
    expect(renderingText(job({ frames_done: 71, frames_total: 72 }))).toBe('Rendering 98%')
  })

  it('names the step once frames are not counted', () => {
    expect(renderingText(job({ frames_total: 0 }))).toBe('Rendering (starting)')
    expect(renderingText(job({ stage: 'encoding' }))).toBe('Rendering (encoding)')
    expect(renderingText(job({ stage: 'uploading' }))).toBe('Rendering (saving)')
    expect(renderingText(job({ stage: null }))).toBe('Rendering (starting)')
  })
})

describe('requestedText', () => {
  const now = Date.parse('2026-10-29T10:30:00Z')

  it('says just now under a minute', () => {
    expect(requestedText('2026-10-29T10:29:30Z', now)).toBe('just now')
  })

  it('gives the time since otherwise', () => {
    expect(requestedText('2026-10-29T10:18:00Z', now)).toBe('12 min ago')
  })

  it('is empty for an unreadable time', () => {
    expect(requestedText('not a date', now)).toBe('')
  })
})

describe('finishedNote', () => {
  it('says a kept video was kept', () => {
    expect(finishedNote(job({ status: 'kept' }))).toContain('existing video was kept')
  })

  it('says how many frames could not be read', () => {
    expect(finishedNote(job({ status: 'rendered', frames_done: 60, frames_total: 72 }))).toBe(
      '12 of 72 frames could not be read.'
    )
  })

  it('has nothing to add for a complete video', () => {
    expect(finishedNote(job({ status: 'rendered', frames_done: 72, frames_total: 72 }))).toBeNull()
    expect(
      finishedNote(job({ status: 'rendered', frames_done: null, frames_total: null }))
    ).toBeNull()
  })
})
