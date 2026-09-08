/** How a plate's capture times are written on screen.
 *
 * A GraviScan sits on a bench at Salk in La Jolla and captures on a fixed
 * cadence in local time, so the capture time belongs to the instrument, not to
 * whoever is reading it. Two people discussing one plate should quote the same
 * number, and a wave should not appear to start in the middle of the night to a
 * collaborator abroad.
 *
 * Both the zone and the locale are fixed, which is also what stops the React
 * hydration error these pages used to throw: unpinned, the same instant renders
 * as "Jun 17, 3:32 AM" in the UTC container and "Jun 16, 8:32 PM" in a Pacific
 * browser, and an en-GB reader gets "17 Jun, 3:32" from the same code. Pinned,
 * the string is a pure function of the instant and cannot disagree with itself.
 *
 * Do not change these to the reader's own zone or locale.
 */

/** Where the scanners are. Not the reader's zone, deliberately. */
export const SCANNER_TIME_ZONE = 'America/Los_Angeles'

/** Fixed so date order and month names cannot vary by machine. */
const SCANNER_LOCALE = 'en-US'

/** Said once on a page, so a reader outside Pacific knows it is deliberate. */
export const SCANNER_TIME_NOTE = "Capture times are the scanner's local time in La Jolla (Pacific)."

const DATE: Intl.DateTimeFormatOptions = {
  year: 'numeric',
  month: 'short',
  day: 'numeric',
}

const TIME: Intl.DateTimeFormatOptions = {
  hour: 'numeric',
  minute: '2-digit',
}

/** A clock time, naming its zone: "Jun 16, 2026, 8:32 PM PDT".
 *
 * `named` is for a time that has to describe itself out of context — read
 * aloud, screenshotted, pasted into a message. Where a page already says which
 * clock it uses, pass false rather than repeating the zone on every entry.
 */
export function formatCaptureTime(
  iso: string | null | undefined,
  { year = true, named = true }: { year?: boolean; named?: boolean } = {}
): string | null {
  return format(iso, {
    ...(year ? DATE : { month: 'short', day: 'numeric' }),
    ...TIME,
    ...(named ? { timeZoneName: 'short' as const } : {}),
  })
}

/** A date alone: "Jun 16, 2026".
 *
 * Unlabelled — a zone on a bare date reads oddly — but pinned all the same. A
 * capture at 03:32 UTC falls on Jun 16 in Pacific and Jun 17 in UTC, so an
 * unpinned date is wrong by a day with nothing on screen to show it.
 */
export function formatCaptureDate(
  iso: string | null | undefined,
  opts: Intl.DateTimeFormatOptions = DATE
): string | null {
  return format(iso, opts)
}

function format(iso: string | null | undefined, opts: Intl.DateTimeFormatOptions): string | null {
  if (!iso) return null
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return null
  return d.toLocaleString(SCANNER_LOCALE, {
    ...opts,
    timeZone: SCANNER_TIME_ZONE,
  })
}
