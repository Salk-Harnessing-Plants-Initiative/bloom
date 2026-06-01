/**
 * Presentational card for one (experiment, wave_number) pair recently
 * uploaded on a plate scanner. Consumed by <ScannerSection /> inside the
 * home-page "Recent phenotypes by plate scanner" widget.
 *
 * Unlike CylScanCard, this card has no "View →" button — the gravi detail
 * page doesn't exist yet. Drop a Next.js Link wrapper + View pill once the
 * gravi experiment detail route ships.
 */

import type { PlateScanRow } from "@/lib/queries/recent-phenotypes-by-plate-scanner";
import { formatRelativeAndAbsolute } from "@/components/recent-phenotypes-by-cyl-scanner/format-times";

interface PlateScanCardProps {
  row: PlateScanRow;
}

/** Compose the wave + scan_mode label. */
function waveLabel(row: PlateScanRow): string {
  const wave = row.wave_number !== null ? `Wave ${row.wave_number}` : "Wave";
  return row.scan_mode ? `${wave} • ${row.scan_mode}` : wave;
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
  const time = formatRelativeAndAbsolute(row.latest_upload_on_this_scanner_at);

  const line3 = phenotyper
    ? `Uploaded by ${phenotyper} • ${row.scanner_name ?? "Unknown scanner"}`
    : `Uploaded on ${row.scanner_name ?? "Unknown scanner"}`;

  return (
    <div className="rounded-lg border border-stone-200 bg-white p-4">
      <h4 className="text-base font-semibold text-stone-900 leading-snug">
        {row.experiment_name}
      </h4>
      <p className="mt-1 text-sm text-stone-600">{waveLabel(row)}</p>
      <p className="mt-1 text-sm text-stone-600">{line3}</p>
      {time && (
        <p className="mt-3 text-xs text-stone-500">{time}</p>
      )}
    </div>
  );
}
