// Shared constants for the embedtree similarity-tree UI.
//
// The species color palette is the single source of truth for visual
// species identity across the page (graph nodes, results rows, legend).
// Keys are common_name values from public.species.

export const DEFAULT_K = 20;
export const K_MIN = 5;
export const K_MAX = 50;

export const SPECIES_COLORS: Record<string, string> = {
  arabidopsis: "#3b82f6", // blue
  rice: "#10b981",        // green
  maize: "#f59e0b",       // amber
  soybean: "#8b5cf6",     // violet
};

export const UNKNOWN_SPECIES_COLOR = "#9ca3af"; // neutral-400

export function speciesColor(common_name: string | null | undefined): string {
  if (!common_name) return UNKNOWN_SPECIES_COLOR;
  return SPECIES_COLORS[common_name.toLowerCase()] ?? UNKNOWN_SPECIES_COLOR;
}

// Single source of truth for the disclaimer copy. Edits land in one place.
export const SIMILARITY_DISCLAIMER =
  "Neighbors are ranked by ESM-2 protein-embedding cosine similarity — " +
  "a quick proxy for orthology, not a verified ortholog set. " +
  "For sequence-alignment-based orthology, use OrthoBrowser.";
