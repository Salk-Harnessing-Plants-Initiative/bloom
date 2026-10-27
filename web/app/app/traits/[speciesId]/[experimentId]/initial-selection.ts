/**
 * Which wave and plant age the traits page shows for a trait's rows.
 *
 * The default is the last wave, at the oldest plant age *within that wave*, so the default always
 * has data (the last wave is often the youngest, so the oldest age overall may not occur in it).
 * A preference, meaning the `?wave=&age=` a pipeline run links with or a wave/age the user picked,
 * wins whenever the trait has rows for it. When it doesn't, the default applies and the returned
 * note says what was asked for and what is shown.
 */

export interface Selection {
  wave: number;
  age: number;
}

export interface SelectionRow {
  wave_number: number;
  plant_age_days: number;
}

export interface ResolvedSelection {
  selection: Selection | null;
  note: string | null;
}

export function resolveSelection(
  rows: SelectionRow[],
  preferred: Partial<Selection> | null,
): ResolvedSelection {
  const wanted = preferred ?? {};
  const asked = wanted.wave !== undefined || wanted.age !== undefined;

  if (rows.length === 0) {
    return {
      selection: null,
      note: asked ? `${formatSelection(wanted)} has no data for this trait.` : null,
    };
  }

  const fallback = defaultSelection(rows);
  if (!asked) return { selection: fallback, note: null };

  // Rows matching whatever was asked for; a missing half matches anything.
  const matching = rows.filter(
    (r) =>
      (wanted.wave === undefined || r.wave_number === wanted.wave) &&
      (wanted.age === undefined || r.plant_age_days === wanted.age),
  );

  if (matching.length === 0) {
    return {
      selection: fallback,
      note:
        `${formatSelection(wanted)} has no data for this trait; ` +
        `showing ${formatSelection(fallback).toLowerCase()}.`,
    };
  }

  // Fill in a missing half the way the default does: the last wave, or the oldest age.
  return {
    selection: {
      wave: wanted.wave ?? max(matching.map((r) => r.wave_number)),
      age: wanted.age ?? max(matching.map((r) => r.plant_age_days)),
    },
    note: null,
  };
}

function defaultSelection(rows: SelectionRow[]): Selection {
  const wave = max(rows.map((r) => r.wave_number));
  const age = max(rows.filter((r) => r.wave_number === wave).map((r) => r.plant_age_days));
  return { wave, age };
}

/** `Math.max(...values)` without the argument-count limit of a spread. */
function max(values: number[]): number {
  return values.reduce((a, b) => (b > a ? b : a), -Infinity);
}

function formatSelection({ wave, age }: Partial<Selection>): string {
  if (wave !== undefined && age !== undefined) return `Wave ${wave} · day ${age}`;
  if (wave !== undefined) return `Wave ${wave}`;
  return `Day ${age}`;
}
