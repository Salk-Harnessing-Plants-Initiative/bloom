/**
 * One scanner's row in the home-page "Recent phenotypes by plate scanner"
 * widget. Layout mirrors the cyl ScannerSection: label-left + cards-right
 * on wide screens, stacked on narrow.
 */

import type { PlateScannerSection } from "@/lib/queries/recent-phenotypes-by-plate-scanner";
import { PlateScanCard } from "./PlateScanCard";
import { ScannerLabel } from "./ScannerLabel";

interface ScannerSectionProps {
  section: PlateScannerSection;
}

export function ScannerSection({ section }: ScannerSectionProps) {
  if (section.cards.length === 0) return null;

  return (
    <div className="flex flex-col gap-2 sm:flex-row sm:items-start sm:gap-5">
      <div className="sm:w-36 sm:flex-shrink-0">
        <ScannerLabel name={section.scanner_name} />
      </div>
      <div className="grid flex-1 gap-2 sm:grid-cols-2">
        {section.cards.map((row) => (
          <PlateScanCard
            key={`${row.experiment_id}-${row.wave_number ?? "novalewave"}`}
            row={row}
          />
        ))}
      </div>
    </div>
  );
}
