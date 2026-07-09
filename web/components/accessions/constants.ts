// Shared constants for the single-species Arabidopsis accession comparison UI.
//
// Unlike the cross-species embedtree (a fixed handful of species), accessions
// are open-ended (~1000+ in the 1001 Genomes panel), so colors are derived
// deterministically from the accession id rather than a hand-maintained map.

export const DEFAULT_K = 20;
export const K_MIN = 5;
export const K_MAX = 50;

// If every similarity in a per-gene ranking falls within this band of the
// top value, the ordering is within numerical noise (accession variants that
// differ by few/no residues) and the UI says so rather than implying a
// confident ranking.
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
  "call. Accession variants often differ by few residues, so small similarity " +
  "gaps may be within noise.";
