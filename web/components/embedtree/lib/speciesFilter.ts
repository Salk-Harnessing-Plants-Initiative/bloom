/**
 * Resolve the `species_filter` argument for the `knn_search_esm2` RPC from the
 * UI's selected-species set.
 *
 * Mirrors the RPC, which treats NULL / empty as "all species": both "every
 * species selected" and "none selected" mean the unfiltered global KNN, so we
 * return null in those cases and only send an explicit subset otherwise. The
 * subset is sorted so the argument is stable (predictable tests + request
 * de-duplication).
 */
export function resolveSpeciesFilter(
  selected: ReadonlySet<string>,
  total: number,
): string[] | null {
  if (selected.size === 0 || selected.size >= total) return null;
  return [...selected].sort();
}
