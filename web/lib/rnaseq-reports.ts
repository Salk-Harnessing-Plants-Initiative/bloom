/**
 * A Cell Ranger run's reports, which the pipeline stores in the scrna bucket at
 * reports/<run id>/: Cell Ranger's web summary and its metrics.
 */

import type { SupabaseClient } from "@supabase/supabase-js";
import type { Database } from "@/lib/database.types";

export const REPORTS_BUCKET = "scrna";
export const WEB_SUMMARY = "web_summary.html";
export const METRICS = "metrics_summary.csv";

// The Cell Ranger metrics shown for a dataset, in this order, as Cell Ranger names them.
export const KEY_METRICS = [
  "Estimated Number of Cells",
  "Median Genes per Cell",
  "Mean Reads per Cell",
  "Reads Mapped Confidently to Genome",
  "Fraction Reads in Cells",
  "Sequencing Saturation",
] as const;

export function reportPath(runId: number, file: string): string {
  return `reports/${runId}/${file}`;
}

/** The run's report page. */
export function reportHref(runId: number): string {
  return `/app/rnaseq-runs/${runId}/report`;
}

/** Which of the run's report files are stored; none if the folder can't be read. */
export async function storedReports(
  supabase: SupabaseClient<Database>,
  runId: number,
): Promise<Set<string>> {
  const { data, error } = await supabase.storage.from(REPORTS_BUCKET).list(`reports/${runId}`);
  if (error || !data) return new Set();
  return new Set(data.map((f) => f.name));
}

/** A stored report file's text, or null if it isn't there or can't be read. */
export async function readReport(
  supabase: SupabaseClient<Database>,
  runId: number,
  file: string,
): Promise<string | null> {
  const { data, error } = await supabase.storage.from(REPORTS_BUCKET).download(reportPath(runId, file));
  if (error || !data) return null;
  return data.text();
}

/** One CSV line's fields; a quoted field may hold commas ("5,066") and doubled quotes. */
function csvFields(line: string): string[] {
  const fields: string[] = [];
  let field = "";
  let quoted = false;
  for (let i = 0; i < line.length; i++) {
    const c = line[i];
    if (quoted) {
      if (c === '"' && line[i + 1] === '"') {
        field += '"';
        i++;
      } else if (c === '"') {
        quoted = false;
      } else {
        field += c;
      }
    } else if (c === '"') {
      quoted = true;
    } else if (c === ",") {
      fields.push(field);
      field = "";
    } else {
      field += c;
    }
  }
  fields.push(field);
  return fields;
}

/** Cell Ranger's metrics_summary.csv (a header line and one line of values) as name -> value. */
export function parseMetrics(csv: string): Map<string, string> {
  const [header, values] = csv.split(/\r?\n/).filter((line) => line.trim() !== "");
  if (!header || !values) return new Map();
  const names = csvFields(header);
  const row = csvFields(values);
  return new Map(names.map((name, i) => [name.trim(), (row[i] ?? "").trim()]));
}

/** The key metrics the run reported, in KEY_METRICS order, leaving out any it didn't. */
export function keyMetrics(metrics: Map<string, string>): [string, string][] {
  return KEY_METRICS.filter((name) => metrics.get(name)).map((name) => [name, metrics.get(name)!]);
}
