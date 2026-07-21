import Link from "next/link";

export function capitalizeFirstLetter(s: string) {
  return s.charAt(0).toUpperCase() + s.slice(1);
}

export function formatExperimentName(name: string) {
  return capitalizeFirstLetter(name.replaceAll("-", " "));
}

export interface Crumb {
  label: string;
  href?: string;
  capitalize?: boolean;
}

// Breadcrumb matching the species/experiment pages: leading crumbs muted, the
// final (current) crumb in default color.
export function Breadcrumb({ trail }: { trail: Crumb[] }) {
  return (
    <div className="text-xl mb-6 select-none">
      {trail.map((crumb, i) => {
        const isLast = i === trail.length - 1;
        const label = (
          <span className={crumb.capitalize ? "capitalize" : undefined}>
            {crumb.label}
          </span>
        );
        return (
          <span key={i} className={isLast ? undefined : "text-stone-400"}>
            {crumb.href ? (
              <span className="hover:underline">
                <Link href={crumb.href}>{label}</Link>
              </span>
            ) : (
              label
            )}
            {!isLast && <>&nbsp;▸&nbsp;</>}
          </span>
        );
      })}
    </div>
  );
}

// Simple title + plate-count header for the per-wave plates page.
export function ExperimentHeaderless({
  title,
  plateCount,
}: {
  title: string;
  plateCount: number;
}) {
  return (
    <div className="mb-6">
      <div className="text-3xl font-serif italic mb-1 select-none">{title}</div>
      <div className="mt-2 text-lg font-medium text-stone-700">
        {plateCount} plate{plateCount === 1 ? "" : "s"}
      </div>
    </div>
  );
}

function formatDate(
  iso: string,
  opts: Intl.DateTimeFormatOptions = {
    year: "numeric",
    month: "short",
    day: "numeric",
  },
): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "—";
  return d.toLocaleDateString(undefined, opts);
}

// "Scanned May 5, 2026" for a single day, "May 1 – May 8, 2026" for a range
// (year dropped from the start when both ends share a year).
export function formatScanDateRange(
  first: string | null,
  last: string | null,
): string | null {
  if (!first && !last) return null;
  const a = first ?? last!;
  const b = last ?? first!;
  if (a === b) return formatDate(a);
  const sameYear = new Date(a).getFullYear() === new Date(b).getFullYear();
  const start = sameYear
    ? formatDate(a, { month: "short", day: "numeric" })
    : formatDate(a);
  return `${start} – ${formatDate(b)}`;
}
