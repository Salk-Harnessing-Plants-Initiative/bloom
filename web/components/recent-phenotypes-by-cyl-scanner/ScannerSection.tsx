/**
 * One scanner's row in the home-page "Recent phenotypes by cylinder
 * scanner" widget.
 *
 * Layout: on wide screens the scanner label sits to the left of the cards
 * (`sm:flex-row`), so 4+ scanners stack as a compact list of horizontal
 * rows. On narrow screens the label sits above the cards (`flex-col`).
 */

import type { CylScannerSection } from "@/lib/queries/recent-phenotypes-by-cyl-scanner";
import { CylScanCard } from "./CylScanCard";
import { ScannerLabel } from "./ScannerLabel";

interface ScannerSectionProps {
  section: CylScannerSection;
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
          <CylScanCard key={`${row.experiment_id}-${row.wave_id}`} row={row} />
        ))}
      </div>
    </div>
  );
}
