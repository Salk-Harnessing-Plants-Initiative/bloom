// Pure helpers for the cylinder plant search, extracted so they can be
// unit-tested without importing the client component.

// Cap the pasted barcode batch so an oversized list can't hit PostgREST unbounded.
export const MAX_BATCH = 200;

// Auto-jump reads up to this many matches; we fetch +1 to detect truncation and,
// when the set is truncated, fall back to the dropdown instead of jumping on it.
export const MAX_JUMP_MATCHES = 500;

// Per-field deep links: species -> species page, experiment -> experiment page,
// accession/barcode -> the accession-in-wave page. Null when ids are missing.
export function fieldHrefs(item: any) {
  const { species_id, experiment_id, wave_id, accession_id } = item;
  const species =
    species_id != null ? `/app/phenotypes/${species_id}` : null;
  const experiment =
    species != null && experiment_id != null
      ? `/app/phenotypes/${species_id}/${experiment_id}`
      : null;
  const accession =
    experiment != null && wave_id != null && accession_id != null
      ? `/app/phenotypes/${species_id}/${experiment_id}/${wave_id}/${accession_id}`
      : null;
  return { species, experiment, accession };
}

// A comma or newline in the input means "batch barcode list"; otherwise the
// input is a single free-text term.
export function parseQuery(input: string): { list: string[] | null; text: string } {
  const raw = input.trim();
  if (/[\n,]/.test(raw)) {
    const list = raw.split(/[\s,]+/).map((t) => t.trim()).filter(Boolean);
    return { list, text: raw };
  }
  return { list: null, text: raw };
}

// Escape ILIKE wildcards so user input matches literally, not as a pattern.
export function escapeLike(s: string): string {
  return s.replace(/[\\%_]/g, (c) => `\\${c}`);
}

// Double-quote a value for the PostgREST .or() grammar, which is what lets it
// carry the grammar's own reserved characters (, . ( ) ") literally.
export function quoteOrValue(s: string): string {
  return `"${s.replace(/["\\]/g, (c) => `\\${c}`)}"`;
}

// A .or() filter matching `term` as a literal substring of any of `columns`.
export function ilikeAnyFilter(columns: string[], term: string): string {
  const pattern = quoteOrValue(`%${escapeLike(term)}%`);
  return columns.map((c) => `${c}.ilike.${pattern}`).join(',');
}

// The one destination every matched row shares, or null when the set is empty,
// truncated (the rest are unknown), or spans more than one destination.
export function singleDestination(
  rows: any[] | null,
  max: number = MAX_JUMP_MATCHES,
): string | null {
  if (!rows || rows.length === 0 || rows.length > max) return null;
  const dests = Array.from(
    new Set(rows.map((r) => fieldHrefs(r).accession).filter(Boolean)),
  );
  return dests.length === 1 ? (dests[0] as string) : null;
}

export type JumpFetchers = {
  species: () => Promise<any[] | null>;
  barcode: () => Promise<any[] | null>;
  accession: () => Promise<any[] | null>;
};

// Enter/magnifier jump order: exact species -> exact barcode -> exact accession.
// Each fetcher runs only if the earlier ones found nothing, so a species hit
// costs one query. Null means "no unambiguous target" — show the dropdown.
export async function resolveJumpTarget(
  fetchers: JumpFetchers,
  max: number = MAX_JUMP_MATCHES,
): Promise<string | null> {
  const species = await fetchers.species();
  if (species && species.length === 1) {
    return fieldHrefs({ species_id: (species[0] as any).id }).species;
  }
  const barcode = singleDestination(await fetchers.barcode(), max);
  if (barcode) return barcode;
  return singleDestination(await fetchers.accession(), max);
}

// Cap notice for a batch search: the pasted list was trimmed before querying,
// or the rows themselves came back at the limit. Barcodes are unique per wave
// only, so <=max barcodes can still match more than max rows.
export function batchNotice(
  listLength: number,
  rowCount: number,
  max: number = MAX_BATCH,
): string {
  if (listLength > max) return `Too many barcodes — searching the first ${max}.`;
  if (rowCount >= max) return `Showing the first ${max} matches — refine your list to see the rest.`;
  return '';
}
