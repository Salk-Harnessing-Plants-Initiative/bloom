import { afterEach, describe, expect, it } from 'vitest'
import { formatScannerTime } from './plate-times'

const OPTS: Intl.DateTimeFormatOptions = {
  year: 'numeric',
  month: 'short',
  day: 'numeric',
  hour: 'numeric',
  minute: '2-digit',
  timeZoneName: 'short',
}

// 03:32 UTC on the 17th is 20:32 Pacific on the 16th — the instant from the
// reported hydration error, and a date boundary in its own right.
const LATE_UTC = '2026-06-17T03:32:00Z'

const tz = process.env.TZ
afterEach(() => {
  process.env.TZ = tz
})

describe('formatScannerTime', () => {
  it('reads the same whatever zone the machine is in', () => {
    // The hydration error was the server (UTC) and the browser disagreeing.
    const seen = new Set<string>()
    for (const zone of ['UTC', 'America/Los_Angeles', 'Europe/Berlin', 'Asia/Kolkata']) {
      process.env.TZ = zone
      seen.add(String(formatScannerTime(LATE_UTC, OPTS)))
    }
    expect(seen).toEqual(new Set(['Jun 16, 2026, 8:32 PM PDT']))
  })

  it('shows the Pacific date for a capture late in the UTC day', () => {
    // Read in UTC this is the 17th, and nothing on screen would look wrong.
    expect(formatScannerTime(LATE_UTC, { year: 'numeric', month: 'short', day: 'numeric' })).toBe(
      'Jun 16, 2026'
    )
  })

  it('takes daylight saving from the zone database', () => {
    expect(formatScannerTime('2026-01-15T20:00:00Z', OPTS)).toContain('PST')
    expect(formatScannerTime('2026-06-15T20:00:00Z', OPTS)).toContain('PDT')
  })

  it('reads correctly either side of the spring transition', () => {
    // 2am never happens locally that night; a fixed -08:00 offset would print it.
    expect(formatScannerTime('2026-03-08T09:59:00Z', OPTS)).toContain('1:59 AM PST')
    expect(formatScannerTime('2026-03-08T10:00:00Z', OPTS)).toContain('3:00 AM PDT')
  })

  it('returns null for anything that is not a time', () => {
    expect(formatScannerTime(null, OPTS)).toBeNull()
    expect(formatScannerTime(undefined, OPTS)).toBeNull()
    expect(formatScannerTime('', OPTS)).toBeNull()
    expect(formatScannerTime('not a timestamp', OPTS)).toBeNull()
  })
})
