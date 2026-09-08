/**
 * What a scientist reads, and why it cannot depend on the machine.
 *
 * The pages used to throw a React hydration error on every load: the same
 * capture rendered as "Jun 17, 3:32 AM" in the UTC container and
 * "Jun 16, 8:32 PM" in a Pacific browser. Pinning the zone alone would not
 * have closed it — the locale is a second, independent source of disagreement.
 */

import { execFileSync } from 'node:child_process'
import { afterEach, describe, expect, it } from 'vitest'
import { SCANNER_TIME_ZONE, formatCaptureDate, formatCaptureTime } from './plate-times'

// 03:32 UTC on the 17th is 20:32 Pacific on the 16th — the instant from the
// reported error, and a date boundary in its own right.
const LATE_UTC = '2026-06-17T03:32:00Z'
const WINTER = '2026-01-15T20:00:00Z'
const SUMMER = '2026-06-15T20:00:00Z'

const originalTz = process.env.TZ
afterEach(() => {
  process.env.TZ = originalTz
})

describe('the string cannot depend on the machine', () => {
  it('reads the same in every time zone a reader might have', () => {
    // The hydration error was exactly this disagreement: the server renders in
    // the container's zone and the browser in the reader's.
    const seen = new Set<string>()
    for (const tz of [
      'UTC',
      'America/Los_Angeles',
      'America/New_York',
      'Europe/Berlin',
      'Asia/Kolkata',
      'Pacific/Kiritimati',
    ]) {
      process.env.TZ = tz
      seen.add(String(formatCaptureTime(LATE_UTC)))
    }
    expect(seen.size).toBe(1)
    expect([...seen][0]).toContain('PDT')
  })

  it('reads the same whatever locale the machine defaults to', () => {
    // Node fixes its default locale at startup, so this one runs out of
    // process. It guards the second half of the fix: pinning the zone alone
    // still lets an en-GB container disagree with an en-US browser, which is
    // why the locale is pinned too. The exact-string assertions below catch a
    // dropped pin on any non-en-US runner; this catches it on every runner.
    const script = `
      const d = new Date(${JSON.stringify(LATE_UTC)});
      const opts = { year: "numeric", month: "short", day: "numeric",
                     hour: "numeric", minute: "2-digit",
                     timeZone: ${JSON.stringify(SCANNER_TIME_ZONE)},
                     timeZoneName: "short" };
      process.stdout.write(JSON.stringify({
        pinned: d.toLocaleString("en-US", opts),
        unpinned: d.toLocaleString(undefined, opts),
      }));
    `
    const run = (locale: string) =>
      JSON.parse(
        execFileSync(process.execPath, ['-e', script], {
          env: { ...process.env, LC_ALL: locale, LANG: locale, TZ: 'UTC' },
          encoding: 'utf8',
        })
      )

    const us = run('en-US')
    const gb = run('en-GB')

    expect(gb.pinned).toBe(us.pinned)
    expect(gb.pinned).toBe('Jun 16, 2026, 8:32 PM PDT')
    // The hazard is real: without the locale pin the two disagree.
    expect(gb.unpinned).not.toBe(us.unpinned)
  })

  it("names the scanner's zone, not the reader's", () => {
    expect(SCANNER_TIME_ZONE).toBe('America/Los_Angeles')
  })
})

describe('which clock, and which day', () => {
  it('shows the Pacific date for a capture late in the UTC day', () => {
    // The failure with no visible symptom: read in UTC this is the 17th, and
    // nothing on screen would look wrong.
    expect(formatCaptureDate(LATE_UTC)).toBe('Jun 16, 2026')
    expect(formatCaptureTime(LATE_UTC)).toBe('Jun 16, 2026, 8:32 PM PDT')
  })

  it('takes daylight saving from the zone database, not from us', () => {
    expect(formatCaptureTime(WINTER)).toContain('PST')
    expect(formatCaptureTime(SUMMER)).toContain('PDT')
  })

  it('reads correctly on both sides of a spring transition', () => {
    // 2026-03-08 10:00 UTC is 02:00 PST; 11:00 UTC is 04:00 PDT — the hour
    // that does not exist locally. A fixed -08:00 offset would get one wrong.
    expect(formatCaptureTime('2026-03-08T09:59:00Z')).toContain('PST')
    expect(formatCaptureTime('2026-03-08T11:00:00Z')).toContain('PDT')
  })
})

describe('what the pages ask for', () => {
  it('names the zone on a time that has to describe itself', () => {
    expect(formatCaptureTime(LATE_UTC, { year: false })).toBe('Jun 16, 8:32 PM PDT')
  })

  it('leaves the zone off a strip where the page already says it', () => {
    expect(formatCaptureTime(LATE_UTC, { year: false, named: false })).toBe('Jun 16, 8:32 PM')
  })

  it('leaves a bare date unlabelled', () => {
    expect(formatCaptureDate(LATE_UTC)).not.toMatch(/P[DS]T/)
  })
})

describe('values that are not times', () => {
  it('returns null for a missing timestamp', () => {
    expect(formatCaptureTime(null)).toBeNull()
    expect(formatCaptureTime(undefined)).toBeNull()
    expect(formatCaptureDate('')).toBeNull()
  })

  it('returns null rather than Invalid Date', () => {
    expect(formatCaptureTime('not a timestamp')).toBeNull()
    expect(formatCaptureDate('2026-13-45')).toBeNull()
  })
})
