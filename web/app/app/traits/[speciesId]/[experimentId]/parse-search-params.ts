import type { Selection } from "./initial-selection";

export type SearchParams = Record<string, string | string[] | undefined>;

/**
 * The optional `?wave=&age=` a pipeline run's traits link carries. Each value counts only as a
 * single, plain non-negative integer: no sign, no leading zero (other than 0 itself), no
 * whitespace, no exponent or hex, and within safe-integer range. Zero counts because it is real
 * data: Bloom Desktop accepts any non-negative integer for both wave number and plant age
 * (bloom-desktop src/utils/metadata-validation.ts), and the schema has no CHECK on either. A
 * repeated parameter is ignored rather than guessed at.
 */
export function parseWaveAge(searchParams: SearchParams): Partial<Selection> {
  const out: Partial<Selection> = {};
  const wave = nonNegativeInt(searchParams.wave);
  const age = nonNegativeInt(searchParams.age);
  if (wave !== null) out.wave = wave;
  if (age !== null) out.age = age;
  return out;
}

function nonNegativeInt(value: string | string[] | undefined): number | null {
  if (typeof value !== "string" || !/^(0|[1-9]\d*)$/.test(value)) return null;
  const n = Number(value);
  return Number.isSafeInteger(n) ? n : null;
}
