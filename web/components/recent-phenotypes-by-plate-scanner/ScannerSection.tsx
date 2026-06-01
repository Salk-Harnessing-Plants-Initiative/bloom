/**
 * One scanner's section in the home-page "Recent phenotypes by plate
 * scanner" widget: a heading with the scanner name plus 1–2 PlateScanCards
 * in a row (stacking on narrow screens).
 */

import type { PlateScannerSection } from "@/lib/queries/recent-phenotypes-by-plate-scanner";
import { PlateScanCard } from "./PlateScanCard";

interface ScannerSectionProps {
  section: PlateScannerSection;
}

export function ScannerSection({ section }: ScannerSectionProps) {
  if (section.cards.length === 0) return null;

  return (
    <div className="space-y-2">
      <h3 className="text-sm font-medium uppercase tracking-wide text-stone-500">
        {section.scanner_name}
      </h3>
      <div className="grid gap-3 sm:grid-cols-2">
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
