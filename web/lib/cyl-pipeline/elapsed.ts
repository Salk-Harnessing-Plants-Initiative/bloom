/**
 * Compact elapsed time ("12 min", "1 h 5 min", "2 d 4 h") since a run or scan
 * timestamp. Display only, so millisecond precision is plenty; ordering uses
 * timestamps.ts.
 */

export function formatElapsed(fromIso: string | null | undefined, nowMs: number): string {
  if (!fromIso) return "";
  const from = Date.parse(fromIso);
  if (Number.isNaN(from)) return "";
  const minutes = Math.floor(Math.max(0, nowMs - from) / 60_000);
  if (minutes < 1) return "under 1 min";
  if (minutes < 60) return `${minutes} min`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return minutes % 60 ? `${hours} h ${minutes % 60} min` : `${hours} h`;
  const days = Math.floor(hours / 24);
  return hours % 24 ? `${days} d ${hours % 24} h` : `${days} d`;
}
