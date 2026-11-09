/** Short status text for the Video Jobs table; the Generate buttons keep their own wording. */

import { formatElapsed } from '@/lib/cyl-pipeline/elapsed'
import { VIDEO_JOB_STAGES, countedFrames, type VideoJob } from '@/lib/video-job'

function ordinal(n: number): string {
  const tens = n % 100
  if (tens >= 11 && tens <= 13) return `${n}th`
  return `${n}${{ 1: 'st', 2: 'nd', 3: 'rd' }[n % 10] ?? 'th'}`
}

/** "Queued (next up)", or its place in line: one ahead is 2nd in line. */
export function queuedText(ahead: number | null): string {
  const before = ahead ?? 0
  return before > 0 ? `Queued (${ordinal(before + 1)} in line)` : 'Queued (next up)'
}

/** "Rendering 45%" while frames are counted, else the step it is on. */
export function renderingText(job: VideoJob): string {
  const counted = countedFrames(job)
  if (counted) return `Rendering ${Math.floor((counted.done / counted.total) * 100)}%`
  if (job.stage === VIDEO_JOB_STAGES.encoding) return 'Rendering (encoding)'
  if (job.stage === VIDEO_JOB_STAGES.uploading) return 'Rendering (saving)'
  return 'Rendering (starting)'
}

/** "just now" under a minute, else "12 min ago". */
export function requestedText(createdAt: string, now: number): string {
  const elapsed = formatElapsed(createdAt, now)
  if (!elapsed) return ''
  return Date.parse(createdAt) > now - 60_000 ? 'just now' : `${elapsed} ago`
}

/** A finished job's extra note: a kept video, or frames that could not be read; else null. */
export function finishedNote(job: VideoJob): string | null {
  if (job.status === 'kept') return 'Nothing new to add, so the existing video was kept.'
  const { frames_done: done, frames_total: total } = job
  if (job.status === 'rendered' && done !== null && total !== null && done < total) {
    return `${total - done} of ${total} frames could not be read.`
  }
  return null
}
