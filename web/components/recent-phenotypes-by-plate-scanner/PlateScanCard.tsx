/**
 * Card for one (experiment, wave_number) pair recently uploaded on a plate
 * scanner. The whole card links to /app/plate-phenotypes (species index);
 * per-experiment drilldown lands in a follow-up PR.
 */

import Link from "next/link";
import type { PlateScanRow } from "@/lib/queries/recent-phenotypes-by-plate-scanner";
import { RelativeTime } from "@/components/recent-phenotypes-by-cyl-scanner/RelativeTime";

interface PlateScanCardProps {
  row: PlateScanRow;
}

/** Compose the wave + scan_mode label. */
function waveLabel(row: PlateScanRow): string {
  const wave = row.wave_number !== null ? `Wave ${row.wave_number}` : "Wave";
  return row.scan_mode ? `${wave} · ${row.scan_mode}` : wave;
}

/** Compose the phenotyper display name from first + last. */
function phenotyperLabel(row: PlateScanRow): string | null {
  const parts = [
    row.phenotyper_first_name,
    row.phenotyper_last_name,
  ].filter(Boolean);
  return parts.length > 0 ? parts.join(" ") : null;
}

export function PlateScanCard({ row }: PlateScanCardProps) {
  const phenotyper = phenotyperLabel(row);

  const metadata = waveLabel(row);

  const people = [
    phenotyper ? `Phenotyped by ${phenotyper}` : null,
    row.scientist_name ? `Led by ${row.scientist_name}` : null,
  ]
    .filter(Boolean)
    .join(" · ");

  return (
    <Link
      href="/app/plate-phenotypes"
      className="group flex flex-col rounded-lg border border-stone-200 border-l-4 border-l-lime-500/40 bg-white p-3 transition hover:-translate-y-0.5 hover:border-lime-700 hover:border-l-lime-700 hover:shadow-md"
    >
      <h4 className="text-sm font-semibold text-stone-900 leading-snug line-clamp-1">
        {row.experiment_name}
      </h4>
      <p className="mt-1 text-xs text-stone-600">{metadata}</p>
      {people && <p className="mt-0.5 text-xs text-stone-500">{people}</p>}
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
