import type { Selection } from "./initial-selection";

export type SearchParams = Record<string, string | string[] | undefined>;

/**
 * The optional `?wave=&age=` a pipeline run's traits link carries. Each value counts only as a
 * single, plain positive integer: no sign, no leading zero, no whitespace, no exponent or hex, and
 * within safe-integer range. A repeated parameter is ignored rather than guessed at.
 */
export function parseWaveAge(searchParams: SearchParams): Partial<Selection> {
  const out: Partial<Selection> = {};
  const wave = positiveInt(searchParams.wave);
  const age = positiveInt(searchParams.age);
  if (wave !== null) out.wave = wave;
  if (age !== null) out.age = age;
  return out;
}

function positiveInt(value: string | string[] | undefined): number | null {
  if (typeof value !== "string" || !/^[1-9]\d*$/.test(value)) return null;
  const n = Number(value);
  return Number.isSafeInteger(n) ? n : null;
}
