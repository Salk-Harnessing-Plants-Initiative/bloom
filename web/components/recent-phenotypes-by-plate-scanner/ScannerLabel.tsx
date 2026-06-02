/**
 * Left-rail label for a plate scanner section: small petri-dish icon +
 * scanner name. Mirrors the cyl ScannerLabel shape but with a different
 * glyph so the two widgets read as distinct at a glance.
 */

interface ScannerLabelProps {
  name: string;
}

export function ScannerLabel({ name }: ScannerLabelProps) {
  return (
    <div className="flex flex-row items-center gap-2">
      <PlateGlyph />
      <span className="text-xs font-semibold uppercase tracking-wide text-stone-700 sm:text-sm">
        {name}
      </span>
    </div>
  );
}

/**
 * Minimal square-plate glyph (rounded-corner square with a 3×3 well grid)
 * in the lime accent color — reads as a microplate / agar plate at a glance.
 */
function PlateGlyph() {
  return (
    <svg
      width="20"
      height="20"
      viewBox="0 0 20 20"
      fill="none"
      xmlns="http://www.w3.org/2000/svg"
      aria-hidden="true"
      className="text-lime-700"
    >
      <rect x="2.5" y="2.5" width="15" height="15" rx="2" stroke="currentColor" strokeWidth="1.4" />
      <circle cx="6.5" cy="6.5" r="1.1" fill="currentColor" />
      <circle cx="10" cy="6.5" r="1.1" fill="currentColor" />
      <circle cx="13.5" cy="6.5" r="1.1" fill="currentColor" />
      <circle cx="6.5" cy="10" r="1.1" fill="currentColor" />
      <circle cx="10" cy="10" r="1.1" fill="currentColor" />
      <circle cx="13.5" cy="10" r="1.1" fill="currentColor" />
      <circle cx="6.5" cy="13.5" r="1.1" fill="currentColor" />
      <circle cx="10" cy="13.5" r="1.1" fill="currentColor" />
      <circle cx="13.5" cy="13.5" r="1.1" fill="currentColor" />
    </svg>
  );
}
