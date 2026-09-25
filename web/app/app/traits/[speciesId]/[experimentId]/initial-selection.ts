/**
 * Which wave and plant age the traits page shows for a trait's rows.
 *
 * The defaults are the page's long-standing ones: the last wave and the oldest plant age across
 * the trait's rows. A preference — the `?wave=&age=` a pipeline run links with, or, after a trait
 * change, whatever the user was already looking at — wins whenever the trait has rows for it. When
 * it doesn't, the defaults apply and the returned note says what was asked for and what is shown.
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
  if (rows.length === 0) return { selection: null, note: null };

  const fallback: Selection = {
    wave: Math.max(...rows.map((r) => r.wave_number)),
    age: Math.max(...rows.map((r) => r.plant_age_days)),
  };

  const wanted = preferred ?? {};
  if (wanted.wave === undefined && wanted.age === undefined) {
    return { selection: fallback, note: null };
  }

  // Rows matching whatever was asked for; a missing half matches anything.
  const matching = rows.filter(
    (r) =>
      (wanted.wave === undefined || r.wave_number === wanted.wave) &&
      (wanted.age === undefined || r.plant_age_days === wanted.age),
  );

  if (matching.length === 0) {
    return {
      selection: fallback,
      note: `${describe(wanted)} has no data for this trait; showing ${describe(fallback).toLowerCase()}.`,
    };
  }

  // Fill in a missing half the way the defaults do: the last wave, or the oldest age.
  return {
    selection: {
      wave: wanted.wave ?? Math.max(...matching.map((r) => r.wave_number)),
      age: wanted.age ?? Math.max(...matching.map((r) => r.plant_age_days)),
    },
    note: null,
  };
}

function describe({ wave, age }: Partial<Selection>): string {
  if (wave !== undefined && age !== undefined) return `Wave ${wave} · day ${age}`;
  if (wave !== undefined) return `Wave ${wave}`;
  return `Day ${age}`;
}
