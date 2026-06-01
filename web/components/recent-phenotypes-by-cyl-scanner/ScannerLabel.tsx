/**
 * Left-rail label for a scanner section: a small cylinder icon stacked
 * above the scanner name. Used inside <ScannerSection>.
 */

interface ScannerLabelProps {
  name: string;
}

export function ScannerLabel({ name }: ScannerLabelProps) {
  return (
    <div className="flex flex-row items-center gap-2 sm:flex-col sm:items-start sm:gap-1.5 sm:pt-1">
      <CylinderGlyph />
      <span className="text-xs font-semibold uppercase tracking-wide text-stone-700 sm:text-sm">
        {name}
      </span>
    </div>
  );
}

/** Minimal cylindrical-tube glyph in the lime accent color. */
function CylinderGlyph() {
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
      <ellipse cx="10" cy="4" rx="6" ry="2" stroke="currentColor" strokeWidth="1.4" />
      <path d="M4 4v12c0 1.1 2.7 2 6 2s6-.9 6-2V4" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" />
      <ellipse cx="10" cy="16" rx="6" ry="2" stroke="currentColor" strokeWidth="1.4" opacity="0.5" />
    </svg>
  );
}
