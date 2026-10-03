/**
 * The 8 production model cards in the wandb registry `sleap-roots-models`, as
 * GET /model-cards serves them, read live on 2026-10-02 (bloom#971). They match
 * talmolab/sleap-roots-predict `tests/card_builders.py` `_PRODUCTION` at 79939ee.
 */

import type { ModelCardEntry } from "../model-windows";

const REGISTRY = "eberrigan-salk-institute-for-biological-studies-org/wandb-registry-sleap-roots-models";

const card = (
  root_type: string,
  collection: string,
  selectors: [string, string, number, number][],
): ModelCardEntry => ({
  root_type,
  registry_id: `${REGISTRY}/${collection}`,
  version: "v0",
  selectors: selectors.map(([species, mode, age_min, age_max]) => ({ species, mode, age_min, age_max })),
});

export const PRODUCTION_CARDS: ModelCardEntry[] = [
  card("crown", "rice-older-crown-221208_113552.multi_instance.n-574", [["rice", "cylinder", 6, 10]]),
  card("crown", "rice-younger-crown-220821_163331.multi_instance.n-867", [["rice", "cylinder", 2, 5]]),
  card("lateral", "arabidopsis-lateral-240130_140452.multi_instance.n-337", [
    ["arabidopsis", "cylinder", 2, 14],
    ["arabidopsis", "multiplant cylinder", 2, 14],
  ]),
  card("lateral", "canola-lateral-240611_083419.multi_instance.n-631", [
    ["canola", "cylinder", 2, 13],
    ["pennycress", "cylinder", 2, 14],
  ]),
  card("lateral", "soybean-lateral-lateral_root_221006_172103.multi_instance.n-482", [["soybean", "cylinder", 2, 8]]),
  card("primary", "canola_pennycress_arabidopsis-primary-240611_102513.multi_instance.n-743", [
    ["arabidopsis", "cylinder", 2, 14],
    ["arabidopsis", "multiplant cylinder", 2, 14],
    ["canola", "cylinder", 2, 13],
    ["pennycress", "cylinder", 2, 14],
  ]),
  card("primary", "rice-younger-primary-230104_182346.multi_instance.n-720", [["rice", "cylinder", 2, 5]]),
  card("primary", "soybean-primary-221003_111420.multi_instance.n-1389", [["soybean", "cylinder", 2, 8]]),
];
