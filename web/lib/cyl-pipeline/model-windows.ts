/**
 * The confirm dialog's model rule (bloom#971 phase 1): classifies each model
 * group (the resolved-params groups of scans that have images) against the
 * production model cards, as the cluster's predictor will treat it.
 *
 * - Past its window: the species + mode has selectors and the age is above the
 *   largest `age_max` among them. The predictor then matches it at that
 *   maximum (sleap-roots-predict `past_window_age`, no overrides).
 * - No model: not past its window, and no such selector covers the age, so the
 *   predictor selects no model for any root type and the scan fails.
 * - Covered otherwise.
 *
 * Kept apart from params-summary.ts, which #897 must not extend. Like those
 * groups, this is throwaway once a server-side preview exists (#898).
 */

import type { ParamsGroup } from "./params-summary";

export interface CardSelector {
  species: string;
  mode: string;
  age_min: number;
  age_max: number;
}

export interface ModelCardEntry {
  root_type: string;
  registry_id: string;
  version: string;
  selectors: CardSelector[];
}

export interface PastWindowGroup {
  species: string;
  age: number;
  count: number;
  /** The largest `age_max` for the group's species and mode. */
  max: number;
}

export interface NoModelGroup {
  species: string;
  age: number;
  count: number;
}

export interface ModelGroupClasses {
  pastWindow: PastWindowGroup[];
  noModel: NoModelGroup[];
}

export function classifyModelGroups(groups: ParamsGroup[], cards: ModelCardEntry[]): ModelGroupClasses {
  const pastWindow: PastWindowGroup[] = [];
  const noModel: NoModelGroup[] = [];
  for (const g of groups) {
    const selectors = cards.flatMap((c) => c.selectors).filter((s) => s.species === g.species && s.mode === g.mode);
    const max = selectors.length > 0 ? Math.max(...selectors.map((s) => s.age_max)) : null;
    if (max !== null && g.age > max) {
      pastWindow.push({ species: g.species, age: g.age, count: g.count, max });
    } else if (!selectors.some((s) => s.age_min <= g.age && g.age <= s.age_max)) {
      noModel.push({ species: g.species, age: g.age, count: g.count });
    }
  }
  return { pastWindow, noModel };
}
