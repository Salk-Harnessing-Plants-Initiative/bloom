/**
 * Presentational card for one (experiment, wave) pair recently uploaded on a
 * cylinder scanner. Consumed by <ScannerSection /> inside the home-page
 * "Recent phenotypes by cylinder scanner" widget.
 *
 * The whole card is a Next.js Link to the existing experiment detail page
 * at /app/phenotypes/{species_id}/{experiment_id}.
 *
 * Scanner name lives in <ScannerSection>'s heading, so it's intentionally
 * omitted from the card body to keep cards compact.
 */

import Link from "next/link";
import type { CylScanRow } from "@/lib/queries/recent-phenotypes-by-cyl-scanner";
import { RelativeTime } from "./RelativeTime";

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

  // Wave • Plant age • Phenotyper, all in one line; skip whichever is null.
  const metadata = [
    wave,
    row.plant_age_days !== null ? `Plant age ${row.plant_age_days}d` : null,
    phenotyper,
  ]
    .filter(Boolean)
    .join(" · ");

  return (
    <Link
      href={href}
      className="group flex flex-col rounded-lg border border-stone-200 border-l-4 border-l-lime-500/40 bg-white p-3 transition hover:-translate-y-0.5 hover:border-lime-700 hover:border-l-lime-700 hover:shadow-md"
    >
      <h4 className="text-sm font-semibold text-stone-900 leading-snug line-clamp-1">
        {row.experiment_name}
      </h4>
      <p className="mt-1 text-xs text-stone-600">{metadata}</p>
      <div className="mt-2 flex items-center justify-between">
        <RelativeTime iso={row.latest_upload_on_this_scanner_at} />
        <span className="inline-flex items-center gap-1 rounded-md border border-stone-300 px-2 py-0.5 text-xs font-medium text-stone-700 transition group-hover:border-lime-700 group-hover:bg-lime-50 group-hover:text-lime-800">
          View
          <span aria-hidden="true">→</span>
        </span>
      </div>
    </Link>
  );
}
