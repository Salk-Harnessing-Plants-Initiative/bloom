// Shared query + URL helpers for the cylinder advanced plant search.

export type AdvancedFilters = {
  barcodes: string[];
  accessionIds: number[];
  speciesIds: number[];
  experimentIds: number[];
};

export type AdvancedResult = {
  rows: any[];
  notFound: string[];
  truncated: boolean;
};

const CAP = 500;
const CHUNK = 150;

const chunk = (arr: string[], n: number): string[][] => {
  const out: string[][] = [];
  for (let i = 0; i < arr.length; i += n) out.push(arr.slice(i, i + n));
  return out;
};

export const parseBarcodes = (s: string): string[] =>
  s.split(/[\s,]+/).map((t) => t.trim()).filter(Boolean);

export const filtersEmpty = (f: AdvancedFilters): boolean =>
  !f.barcodes.length && !f.accessionIds.length && !f.speciesIds.length && !f.experimentIds.length;

// AND across fields; OR within a field (.in). Exact match for list values
// (barcodes contain '_', a LIKE wildcard). Barcode lists are chunked.
export async function runAdvancedSearch(supabase: any, f: AdvancedFilters): Promise<AdvancedResult> {
  const build = (barcodeChunk?: string[]) => {
    let q: any = supabase.from('cyl_plant_search').select('*');
    if (barcodeChunk) q = q.in('qr_code', barcodeChunk);
    if (f.accessionIds.length) q = q.in('accession_id', f.accessionIds);
    if (f.speciesIds.length) q = q.in('species_id', f.speciesIds);
    if (f.experimentIds.length) q = q.in('experiment_id', f.experimentIds);
    return q.limit(CAP);
  };

  let rows: any[] = [];
  if (f.barcodes.length) {
    for (const c of chunk(f.barcodes, CHUNK)) {
      const { data, error } = await build(c);
      if (error) throw error;
      if (data) rows.push(...data);
    }
  } else {
    const { data, error } = await build();
    if (error) throw error;
    rows = data || [];
  }

  const seen = new Set<number>();
  rows = rows.filter((r) => (seen.has(r.plant_id) ? false : (seen.add(r.plant_id), true)));

  const matched = new Set(rows.map((r) => r.qr_code));
  const notFound = f.barcodes.filter((b) => !matched.has(b));
  return { rows: rows.slice(0, CAP), notFound, truncated: rows.length > CAP };
}

export function filtersToParams(f: AdvancedFilters): URLSearchParams {
  const p = new URLSearchParams();
  if (f.barcodes.length) p.set('barcodes', f.barcodes.join(','));
  if (f.accessionIds.length) p.set('acc', f.accessionIds.join(','));
  if (f.speciesIds.length) p.set('sp', f.speciesIds.join(','));
  if (f.experimentIds.length) p.set('exp', f.experimentIds.join(','));
  return p;
}

export function paramsToFilters(sp: URLSearchParams): AdvancedFilters {
  const nums = (v: string | null) =>
    v ? v.split(',').map(Number).filter((n) => !Number.isNaN(n)) : [];
  const bc = sp.get('barcodes');
  return {
    barcodes: bc ? bc.split(',').map((s) => s.trim()).filter(Boolean) : [],
    accessionIds: nums(sp.get('acc')),
    speciesIds: nums(sp.get('sp')),
    experimentIds: nums(sp.get('exp')),
  };
}
