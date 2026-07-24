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
  total: number;
  truncated: boolean;
};

export const parseBarcodes = (s: string): string[] =>
  s.split(/[\s,]+/).map((t) => t.trim()).filter(Boolean);

export const filtersEmpty = (f: AdvancedFilters): boolean =>
  !f.barcodes.length && !f.accessionIds.length && !f.speciesIds.length && !f.experimentIds.length;

// One server-side call: AND across fields, OR within a field. The RPC returns
// the capped page, the true total (for the "showing N of M" note), and the
// pasted barcodes that don't exist. RLS applies via the security_invoker view.
// p_limit is left to the function's own default so the page size lives in one
// place — the RPC clamps it regardless of what a caller asks for.
export async function runAdvancedSearch(supabase: any, f: AdvancedFilters): Promise<AdvancedResult> {
  const { data, error } = await supabase.rpc('cyl_plant_search_query', {
    p_barcodes: f.barcodes,
    p_accession_ids: f.accessionIds,
    p_species_ids: f.speciesIds,
    p_experiment_ids: f.experimentIds,
  });
  if (error) throw error;
  const rows: any[] = data?.rows ?? [];
  const total: number = data?.total ?? 0;
  return { rows, notFound: data?.not_found ?? [], total, truncated: total > rows.length };
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
    v ? v.split(',').map(Number).filter((n) => Number.isInteger(n) && n > 0) : [];
  const bc = sp.get('barcodes');
  return {
    barcodes: bc ? bc.split(',').map((s) => s.trim()).filter(Boolean) : [],
    accessionIds: nums(sp.get('acc')),
    speciesIds: nums(sp.get('sp')),
    experimentIds: nums(sp.get('exp')),
  };
}
