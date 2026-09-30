/**
 * Every scan id of every listed plant, for "Run this accession" (design D7).
 * The accession grid renders only the first scan per day, and nothing for a
 * scan without a frame-1 image or without an age, so the ids come from the
 * plants, not from what is rendered. The page calls it before sorting `cyl_scans` in place, so
 * the ids keep the plants' own order; it never mutates its input.
 */
export function accessionScanIds(plants: readonly { cyl_scans: readonly { id: number }[] }[] | null | undefined): number[] {
  const ids = new Set<number>();
  for (const plant of plants ?? []) for (const scan of plant.cyl_scans) ids.add(scan.id);
  return [...ids];
}
