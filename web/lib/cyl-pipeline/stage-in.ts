/**
 * Metadata that makes a scan fail at stage-in. `bloomctl download_for_predict`
 * resolves each scan's params from its species and plant age, so a blank
 * species, or an age that is null or not a whole number, fails before any
 * prediction (design D4). Shared by the drill-down's likely cause and the
 * confirm dialog's stage-in warning (through params-summary.ts).
 */

import type { ScanMeta } from "./scan-meta";

export type StageInProblem = "species-missing" | "age-missing" | "age-not-whole";

export function stageInProblems(meta: Pick<ScanMeta, "species_name" | "plant_age_days">): StageInProblem[] {
  const problems: StageInProblem[] = [];
  if (!meta.species_name?.trim()) problems.push("species-missing");
  if (meta.plant_age_days == null) problems.push("age-missing");
  else if (!Number.isInteger(meta.plant_age_days)) problems.push("age-not-whole");
  return problems;
}
