// Pure helpers for the cylinder plant search, extracted so they can be
// unit-tested without importing the client component.

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
