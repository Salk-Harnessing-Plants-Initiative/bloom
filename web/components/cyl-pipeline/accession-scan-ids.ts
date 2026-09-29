/**
 * Every scan id of every listed plant, for "Run this accession" (design D7).
 * The accession grid renders only the first frame-1 scan per day, so the ids
 * come from the plants, not from what is rendered. Call it before the page
 * sorts `cyl_scans` in place; it never mutates its input.
 */
export function accessionScanIds(plants: readonly { cyl_scans: readonly { id: number }[] }[] | null | undefined): number[] {
  const ids = new Set<number>();
  for (const plant of plants ?? []) for (const scan of plant.cyl_scans) ids.add(scan.id);
  return [...ids];
}
