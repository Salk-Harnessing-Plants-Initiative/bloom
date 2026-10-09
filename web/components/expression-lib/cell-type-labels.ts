/** Predicted cell types: per-cell labels the dataset marks as cell types, summarised per cluster. */

/** The dataset metadata key listing which cell labels are predicted cell types. */
export const CELL_TYPE_LABELS_KEY = "cell_type_labels";

export interface PredictedSource {
  /** The cell label key, e.g. `periderm_atlas`. */
  key: string;
  /** The label most of the cluster's cells carry from this source. */
  label: string;
}

/** The cell label keys the dataset marks as predicted cell types, in its order; empty when none. */
export function cellTypeLabelKeys(metadata: unknown): string[] {
  if (!metadata || typeof metadata !== "object" || Array.isArray(metadata)) return [];
  const keys = (metadata as Record<string, unknown>)[CELL_TYPE_LABELS_KEY];
  if (!Array.isArray(keys)) return [];
  return [...new Set(keys.filter((k): k is string => typeof k === "string" && k.trim() !== ""))];
}

/** A label key as a person reads it: `periderm_atlas` becomes "Periderm atlas". */
export function sourceName(key: string): string {
  const words = key.replace(/_/g, " ").trim();
  return words.charAt(0).toUpperCase() + words.slice(1);
}

/** Per cluster ordinal, the label most of its cells carry from each source, in the dataset's order. */
export function predictedCellTypes(
  cells: readonly { cluster_ordinal: number; facets?: Record<string, string> | null }[],
  keys: readonly string[],
): Map<number, PredictedSource[]> {
  const out = new Map<number, PredictedSource[]>();
  if (keys.length === 0) return out;

  const counts = new Map<number, Map<string, Map<string, number>>>();
  for (const cell of cells) {
    const byKey = counts.get(cell.cluster_ordinal) ?? new Map<string, Map<string, number>>();
    for (const key of keys) {
      const label = cell.facets?.[key];
      if (label == null || label === "") continue;
      const byLabel = byKey.get(key) ?? new Map<string, number>();
      byLabel.set(label, (byLabel.get(label) ?? 0) + 1);
      byKey.set(key, byLabel);
    }
    counts.set(cell.cluster_ordinal, byKey);
  }

  for (const [ordinal, byKey] of counts) {
    const sources: PredictedSource[] = [];
    for (const key of keys) {
      const [top] = [...(byKey.get(key) ?? new Map<string, number>())]
        .sort(([a, n], [b, m]) => m - n || a.localeCompare(b));
      if (top) sources.push({ key, label: top[0] });
    }
    if (sources.length > 0) out.set(ordinal, sources);
  }
  return out;
}
