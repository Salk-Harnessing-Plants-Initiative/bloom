// Pure helpers for the cylinder plant search, extracted so they can be
// unit-tested without importing the client component.

// Cap the pasted barcode batch so an oversized list can't hit PostgREST unbounded.
export const MAX_BATCH = 200;

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

// More than one token means "batch barcode list". Spaces, tabs, newlines and
// commas all separate, matching parseBarcodes in the advanced panel so the same
// paste behaves the same in both boxes. A single token is a free-text term.
export function parseQuery(input: string): { list: string[] | null; text: string } {
  const raw = input.trim();
  if (!raw) return { list: null, text: '' };
  const parts = raw.split(/[\s,]+/).filter(Boolean);
  if (/[\n,]/.test(raw) || parts.length > 1) return { list: parts, text: raw };
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

// What cyl_plant_search_navigate answers with: a species page, one plant's
// accession page, or nothing unambiguous.
export type NavTarget =
  | { target: 'species'; species_id: number }
  | { target: 'plant'; species_id: number; experiment_id: number; wave_id: number; accession_id: number }
  | { target: 'none' };

// Turn the RPC's answer into an href. The RPC returns ids only — route shape
// stays here — and null means "show the dropdown instead of jumping".
export function navigateHref(nav: NavTarget | null | undefined): string | null {
  if (!nav) return null;
  if (nav.target === 'species') return fieldHrefs({ species_id: nav.species_id }).species;
  if (nav.target === 'plant') return fieldHrefs(nav).accession;
  return null;
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
