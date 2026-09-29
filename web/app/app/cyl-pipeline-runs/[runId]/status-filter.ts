import type { StatusFilter } from "./RunScansTable";

const FILTERS: readonly StatusFilter[] = ["all", "waiting", "recorded", "failed"];

/** The drill-down table's `?status=` filter: one known value, otherwise "all". */
export function parseStatusFilter(value: string | string[] | undefined): StatusFilter {
  return typeof value === "string" && (FILTERS as readonly string[]).includes(value) ? (value as StatusFilter) : "all";
}
