// The scanners sit at Salk in La Jolla, so a capture time belongs to the
// instrument, not to whoever is reading it. Pinning both the zone and the
// locale also makes the string identical on the server and in the browser,
// which is what stops the React hydration error these pages used to throw.
export const SCANNER_TIME_ZONE = 'America/Los_Angeles'

export const SCANNER_TIME_NOTE = "Capture times are the scanner's local time in La Jolla (Pacific)."

export function formatScannerTime(
  iso: string | null | undefined,
  opts: Intl.DateTimeFormatOptions
): string | null {
  if (!iso) return null
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return null
  return d.toLocaleString('en-US', { ...opts, timeZone: SCANNER_TIME_ZONE })
}
