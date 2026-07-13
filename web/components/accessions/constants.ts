// Shared constants for the single-species Arabidopsis accession comparison UI.
//
// Unlike the cross-species embedtree (a fixed handful of species), accessions
// are open-ended (~1000+ in the 1001 Genomes panel), so colors are derived
// deterministically from the accession id rather than a hand-maintained map.

export const DEFAULT_K = 20;
export const K_MIN = 5;
// 1000 — pgvector's hnsw.ef_search ceiling, the most neighbours the index can
// return in one search. knn_search_esm3 already clamps match_count to 1000.
export const K_MAX = 1000;

// Best-match-per-accession returns the top-N nearest proteins PER accession
// (N=1 = one best row each). Separate from the neighbourhood K above.
export const PER_ACCESSION_DEFAULT = 1;
export const PER_ACCESSION_MIN = 1;
export const PER_ACCESSION_MAX = 10;

// Best-match-per-accession shows every accession (one row each), so it requests
// the RPC's hard cap rather than a user-chosen K. ~458 accessions exist today.
export const ALL_ACCESSION_MATCHES = 1000;

// If the ranked (non-reference) variants in a per-gene comparison all fall
// within this cosine band of each other, the ordering is within numerical
// noise (accession variants that differ by few/no residues) and the UI says so
// rather than implying a confident ranking.
// PROVISIONAL: 0.001 is a placeholder until we can calibrate it against real
// ESM-3 pooled embeddings of near-identical accession variants; mean-pooled
// ESM cosine between single-residue variants often sits at 0.99–0.999, so this
// threshold should be revisited once data is loaded.
export const NOISE_EPSILON = 0.001;

// Deterministic HSL color for an accession, keyed on its numeric id so the
// same accession is the same color across surfaces. Golden-angle hue spacing
// keeps adjacent ids visually distinct.
export function accessionColor(accessionId: number | null | undefined): string {
  if (accessionId == null) return "#9ca3af"; // neutral-400
  const hue = (accessionId * 137.508) % 360;
  return `hsl(${hue.toFixed(1)}, 62%, 52%)`;
}

// Single source of truth for the disclaimer copy.
export const ACCESSION_DISCLAIMER =
  "Neighbors and rankings are by ESM-3 protein-embedding cosine similarity — " +
  "a proxy for protein divergence, not a verified functional or orthology " +
  "call.";
