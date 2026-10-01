/**
 * The confirm dialog's resolved params (design D4): a count per distinct
 * `(species, mode, age)`, as `resolve_params` would resolve each scan today
 * (species trimmed and lowercased, mode always `cylinder`, age the plant's
 * age). Display only, and throwaway: a server-side preview (#898) replaces
 * it, and #897 must not extend it here. Scans that would fail at stage-in are
 * counted apart and left out of the groups. No parameter hash is computed.
 */

import type { ScanMeta } from "./scan-meta";
import { stageInProblems } from "./stage-in";

export interface ParamsGroup {
  species: string;
  mode: "cylinder";
  age: number;
  count: number;
}

export interface ParamsSummary {
  /** Largest first, then by species, then by age. */
  groups: ParamsGroup[];
  /** Scans with a blank species, or an age that is null or not a whole number. */
  stageInCount: number;
}

export function paramsSummary(scans: Pick<ScanMeta, "species_name" | "plant_age_days">[]): ParamsSummary {
  const groups = new Map<string, ParamsGroup>();
  let stageInCount = 0;
  for (const scan of scans) {
    if (stageInProblems(scan).length > 0) {
      stageInCount += 1;
      continue;
    }
    // Both non-null here: stageInProblems flags a blank species and a null age.
    const species = scan.species_name!.trim().toLowerCase();
    const age = scan.plant_age_days!;
    const key = JSON.stringify([species, age]);
    const group = groups.get(key);
    if (group) group.count += 1;
    else groups.set(key, { species, mode: "cylinder", age, count: 1 });
  }
  const sorted = [...groups.values()].sort(
    (a, b) => b.count - a.count || (a.species < b.species ? -1 : a.species > b.species ? 1 : 0) || a.age - b.age,
  );
  return { groups: sorted, stageInCount };
}
