/**
 * Presentational card for one (experiment, wave_number) pair recently
 * uploaded on a plate scanner. Consumed by <ScannerSection /> inside the
 * home-page "Recent phenotypes by plate scanner" widget.
 *
 * Unlike CylScanCard, this card has no "View →" button — the gravi detail
 * page doesn't exist yet. Drop a Next.js Link wrapper + View pill once the
 * gravi experiment detail route ships.
 *
 * Scanner name lives in <ScannerSection>'s heading, so it's intentionally
 * omitted from the card body to keep cards compact.
 */

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

  const metadata = [waveLabel(row), phenotyper].filter(Boolean).join(" · ");

  return (
    <div className="flex flex-col rounded-lg border border-stone-200 border-l-4 border-l-lime-500/40 bg-white p-3 transition hover:border-lime-700 hover:border-l-lime-700 hover:shadow-md">
      <h4 className="text-sm font-semibold text-stone-900 leading-snug line-clamp-1">
        {row.experiment_name}
      </h4>
      <p className="mt-1 text-xs text-stone-600">{metadata}</p>
      <div className="mt-2">
        <RelativeTime iso={row.latest_upload_on_this_scanner_at} />
      </div>
    </div>
  );
}
