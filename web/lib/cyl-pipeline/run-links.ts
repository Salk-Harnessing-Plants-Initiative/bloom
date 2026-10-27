/**
 * Run → results links (design D6). They are derived only from a run's
 * `cyl_pipeline_run_scans` rows and those scans' metadata. Nothing links a
 * trait source to its run (bloom#864), and write-back can deliver results for
 * scans a run never requested (sleap-roots-pipeline#71), so a run-keyed trait
 * listing would be wrong either way.
 *
 * The traits page shows the whole experiment at one wave and plant age, so the
 * links pick the run's most common (wave, age) pairs, counted jointly:
 * independent modes could name a combination no scan has.
 */

import type { ScanMeta } from "./scan-meta";

export const TRAITS_LINKS_PER_EXPERIMENT = 3;

export interface TraitsLink {
  wave: number;
  age: number | null;
  count: number;
  href: string;
  label: string;
}

export interface ExperimentTraitsLinks {
  experimentId: number;
  speciesId: number;
  links: TraitsLink[];
}

/** The scan's existing cylinder page, or null when its metadata is incomplete. */
export function scanImagesHref(meta: ScanMeta | undefined): string | null {
  if (!meta) return null;
  const { species_id, experiment_id, wave_id, accession_id, scan_id } = meta;
  if (species_id == null || experiment_id == null || wave_id == null || accession_id == null) return null;
  return `/app/phenotypes/${species_id}/${experiment_id}/${wave_id}/${accession_id}/${scan_id}`;
}

const byAge = (a: number | null, b: number | null) => (a === b ? 0 : a === null ? 1 : b === null ? -1 : a - b);

export function traitsLinks(scanIds: number[], meta: Map<number, ScanMeta>): ExperimentTraitsLinks[] {
  const experiments = new Map<
    number,
    { speciesId: number; scans: number; pairs: Map<string, { wave: number; age: number | null; count: number }> }
  >();
  for (const scanId of scanIds) {
    const m = meta.get(scanId);
    if (!m || m.experiment_id == null || m.species_id == null || m.wave_number == null) continue;
    let exp = experiments.get(m.experiment_id);
    if (!exp) {
      exp = { speciesId: m.species_id, scans: 0, pairs: new Map() };
      experiments.set(m.experiment_id, exp);
    }
    exp.scans += 1;
    const key = `${m.wave_number}:${m.plant_age_days}`;
    const pair = exp.pairs.get(key) ?? { wave: m.wave_number, age: m.plant_age_days, count: 0 };
    pair.count += 1;
    exp.pairs.set(key, pair);
  }

  return [...experiments.entries()]
    .sort(([idA, a], [idB, b]) => b.scans - a.scans || idA - idB)
    .map(([experimentId, exp]) => ({
      experimentId,
      speciesId: exp.speciesId,
      links: [...exp.pairs.values()]
        .sort((a, b) => b.count - a.count || a.wave - b.wave || byAge(a.age, b.age))
        .slice(0, TRAITS_LINKS_PER_EXPERIMENT)
        .map(({ wave, age, count }) => ({
          wave,
          age,
          count,
          href: `/app/traits/${exp.speciesId}/${experimentId}?wave=${wave}${age === null ? "" : `&age=${age}`}`,
          label: `Wave ${wave}${age === null ? "" : ` · day ${age}`} traits (all scans in the experiment, latest result per scan)`,
        })),
    }));
}
