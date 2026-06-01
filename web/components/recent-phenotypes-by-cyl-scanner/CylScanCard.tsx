/**
 * Presentational card for one (experiment, wave) pair recently uploaded on a
 * cylinder scanner. Consumed by <ScannerSection /> inside the home-page
 * "Recent phenotypes by cylinder scanner" widget.
 *
 * The whole card is a Next.js Link to the existing experiment detail page
 * at /app/phenotypes/{species_id}/{experiment_id}.
 */

import Link from "next/link";
import type { CylScanRow } from "@/lib/queries/recent-phenotypes-by-cyl-scanner";
import { formatRelativeAndAbsolute } from "./format-times";

interface CylScanCardProps {
  row: CylScanRow;
}

/** Compose the wave label from wave_name (preferred) or wave_number. */
function waveLabel(row: CylScanRow): string {
  if (row.wave_name) return row.wave_name;
  if (row.wave_number !== null) return `Wave ${row.wave_number}`;
  return "Wave";
}

/** Compose the phenotyper display name from first + last. */
function phenotyperLabel(row: CylScanRow): string | null {
  const parts = [
    row.phenotyper_first_name,
    row.phenotyper_last_name,
  ].filter(Boolean);
  return parts.length > 0 ? parts.join(" ") : null;
}

export function CylScanCard({ row }: CylScanCardProps) {
  const href =
    row.species_id !== null
      ? `/app/phenotypes/${row.species_id}/${row.experiment_id}`
      : `/app/phenotypes`;

  const wave = waveLabel(row);
  const phenotyper = phenotyperLabel(row);
  const time = formatRelativeAndAbsolute(row.latest_upload_on_this_scanner_at);

  // Line 2: wave + plant age (skip plant age if null)
  const line2 =
    row.plant_age_days !== null
      ? `${wave} • Plant age ${row.plant_age_days} days`
      : wave;

  // Line 3: uploaded by + scanner (skip phenotyper if null)
  const line3 = phenotyper
    ? `Uploaded by ${phenotyper} • ${row.scanner_name ?? "Unknown scanner"}`
    : `Uploaded on ${row.scanner_name ?? "Unknown scanner"}`;

  return (
    <Link
      href={href}
      className="group block rounded-lg border border-stone-200 bg-white p-4 transition hover:border-lime-700 hover:shadow-sm"
    >
      <h4 className="text-base font-semibold text-stone-900 leading-snug">
        {row.experiment_name}
      </h4>
      <p className="mt-1 text-sm text-stone-600">{line2}</p>
      <p className="mt-1 text-sm text-stone-600">{line3}</p>
      <div className="mt-3 flex items-center justify-between">
        {time ? (
          <p className="text-xs text-stone-500">{time}</p>
        ) : (
          <span />
        )}
        <span className="inline-flex items-center gap-1 rounded-md border border-stone-300 px-2.5 py-1 text-xs font-medium text-stone-700 transition group-hover:border-lime-700 group-hover:bg-lime-50 group-hover:text-lime-800">
          View
          <span aria-hidden="true">→</span>
        </span>
      </div>
    </Link>
  );
}
