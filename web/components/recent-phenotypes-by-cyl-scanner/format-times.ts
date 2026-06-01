/**
 * Locale-aware "relative + absolute" timestamp formatting for the
 * recent-experiments cards. Uses built-in Intl APIs so we don't pull in
 * date-fns just for this.
 *
 * Exported separately from the component so the formatting logic can be
 * unit-tested without rendering React.
 */

/**
 * Returns "4 hours ago", "yesterday", "in 3 days", etc. Falls back to an
 * empty string if the input isn't a valid date.
 */
export function formatRelative(
  iso: string | Date | null | undefined,
  now: Date = new Date(),
  locale: string | undefined = undefined,
): string {
  if (iso == null) return "";
  const date = typeof iso === "string" ? new Date(iso) : iso;
  if (Number.isNaN(date.getTime())) return "";

  const diffSeconds = (date.getTime() - now.getTime()) / 1000;
  const rtf = new Intl.RelativeTimeFormat(locale, { numeric: "auto" });

  const units: Array<[Intl.RelativeTimeFormatUnit, number]> = [
    ["year", 60 * 60 * 24 * 365],
    ["month", 60 * 60 * 24 * 30],
    ["week", 60 * 60 * 24 * 7],
    ["day", 60 * 60 * 24],
    ["hour", 60 * 60],
    ["minute", 60],
    ["second", 1],
  ];

  for (const [unit, secondsPerUnit] of units) {
    if (Math.abs(diffSeconds) >= secondsPerUnit || unit === "second") {
      return rtf.format(Math.round(diffSeconds / secondsPerUnit), unit);
    }
  }
  return "";
}

/**
 * Returns "May 28, 2026, 8:04 AM" (or locale equivalent). Falls back to an
 * empty string if the input isn't a valid date.
 */
export function formatAbsolute(
  iso: string | Date | null | undefined,
  locale: string | undefined = undefined,
): string {
  if (iso == null) return "";
  const date = typeof iso === "string" ? new Date(iso) : iso;
  if (Number.isNaN(date.getTime())) return "";

  return new Intl.DateTimeFormat(locale, {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(date);
}

/**
 * Combined "4 hours ago • May 28, 2026, 8:04 AM" used on the card's footer
 * line. Skips the bullet if either half is empty.
 */
export function formatRelativeAndAbsolute(
  iso: string | Date | null | undefined,
  now: Date = new Date(),
  locale: string | undefined = undefined,
): string {
  const rel = formatRelative(iso, now, locale);
  const abs = formatAbsolute(iso, locale);
  if (rel && abs) return `${rel} • ${abs}`;
  return rel || abs;
}
