/** Predicted cell types: per-cell labels the dataset marks as cell types, summarised per cluster. */

/** The dataset metadata key listing which cell labels are predicted cell types. */
export const CELL_TYPE_LABELS_KEY = "cell_type_labels";

/** A label shown under a cluster must cover at least this share of its cells. */
export const MIN_SHARE = 0.1;

/** At most this many labels per source under a cluster, largest first. */
export const MAX_PER_SOURCE = 2;

export interface LabelShare {
  label: string;
  /** Share of the cluster's cells carrying the label, 0 to 1. */
  share: number;
}

export interface PredictedSource {
  /** The cell label key, e.g. `periderm_atlas`. */
  key: string;
  labels: LabelShare[];
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

/** Per cluster ordinal, each source's most common labels with their share of the cluster's cells. */
export function predictedCellTypes(
  cells: readonly { cluster_ordinal: number; facets?: Record<string, string> | null }[],
  keys: readonly string[],
): Map<number, PredictedSource[]> {
  const out = new Map<number, PredictedSource[]>();
  if (keys.length === 0) return out;

  const totals = new Map<number, number>();
  const counts = new Map<number, Map<string, Map<string, number>>>();
  for (const cell of cells) {
    totals.set(cell.cluster_ordinal, (totals.get(cell.cluster_ordinal) ?? 0) + 1);
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
    const total = totals.get(ordinal) ?? 0;
    const sources: PredictedSource[] = [];
    for (const key of keys) {
      const labels = [...(byKey.get(key) ?? new Map<string, number>())]
        .map(([label, n]) => ({ label, share: n / total }))
        .filter((l) => l.share >= MIN_SHARE)
        .sort((a, b) => b.share - a.share || a.label.localeCompare(b.label))
        .slice(0, MAX_PER_SOURCE);
      if (labels.length > 0) sources.push({ key, labels });
    }
    if (sources.length > 0) out.set(ordinal, sources);
  }
  return out;
}

/** "Columella 79%, Lateral Root Cap 18%"; a label every cell carries has no percentage. */
export function formatShares(labels: readonly LabelShare[]): string {
  return labels
    .map((l) => (l.share >= 1 ? l.label : `${l.label} ${Math.round(l.share * 100)}%`))
    .join(", ");
}
