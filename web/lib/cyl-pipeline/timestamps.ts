/**
 * Microsecond-exact ordering of Postgres `timestamptz` strings.
 *
 * Realtime and PostgREST both send `YYYY-MM-DDTHH:MM:SS[.f]+00:00`, with the
 * fraction's trailing zeros trimmed (task 0.3's captures). `Date.parse` keeps
 * only milliseconds, and its handling of longer fractions is
 * implementation-defined, so the list's keyset cursor compares these instead.
 */

const TIMESTAMP =
  /^(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2}):(\d{2})(?:\.(\d{1,6}))?(Z|[+-]\d{2}(?::?\d{2})?)$/;

/** Microseconds since the epoch, or null when the value isn't a timestamp. */
export function parseTimestampMicros(value: string | null | undefined): bigint | null {
  if (typeof value !== "string") return null;
  const m = TIMESTAMP.exec(value);
  if (!m) return null;
  const [, y, mo, d, h, mi, s, frac = "", zone] = m;
  const fields = [y, mo, d, h, mi, s].map(Number);
  const ms = Date.UTC(fields[0], fields[1] - 1, fields[2], fields[3], fields[4], fields[5]);
  const back = new Date(ms);
  // Date.UTC rolls over out-of-range fields (Feb 30 → Mar 2); reject those.
  if (
    back.getUTCFullYear() !== fields[0] ||
    back.getUTCMonth() !== fields[1] - 1 ||
    back.getUTCDate() !== fields[2] ||
    back.getUTCHours() !== fields[3] ||
    back.getUTCMinutes() !== fields[4] ||
    back.getUTCSeconds() !== fields[5]
  ) {
    return null;
  }
  let offsetMinutes = 0;
  if (zone !== "Z") {
    const sign = zone[0] === "-" ? -1 : 1;
    const digits = zone.slice(1).replace(":", "");
    const oh = Number(digits.slice(0, 2));
    const om = digits.length > 2 ? Number(digits.slice(2)) : 0;
    if (om > 59) return null;
    offsetMinutes = sign * (oh * 60 + om);
  }
  return BigInt(ms) * BigInt(1000) + BigInt(frac.padEnd(6, "0") || "0") - BigInt(offsetMinutes) * BigInt(60_000_000);
}

/** Ascending comparator. An unparsable value sorts as newest. */
export function compareTimestamps(a: string | null | undefined, b: string | null | undefined): number {
  const x = parseTimestampMicros(a);
  const y = parseTimestampMicros(b);
  if (x === null || y === null) return x === y ? 0 : x === null ? 1 : -1;
  return x < y ? -1 : x > y ? 1 : 0;
}
