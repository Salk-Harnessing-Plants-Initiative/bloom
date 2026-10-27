import { STATUS_LABELS, type RunStatus } from "@/lib/rnaseq-runs";

const STATUS_STYLES: Record<RunStatus, string> = {
  queued: "bg-stone-100 text-stone-700",
  submitted: "bg-sky-50 text-sky-800",
  running: "bg-sky-100 text-sky-900",
  succeeded: "bg-lime-100 text-lime-900",
  skipped: "bg-stone-100 text-stone-700",
  failed: "bg-red-100 text-red-800",
};

/** A run's status as a small coloured label. */
export function StatusBadge({ status }: { status: string }) {
  const known = status in STATUS_STYLES ? (status as RunStatus) : null;
  return (
    <span
      className={`inline-block rounded px-2 py-0.5 text-xs font-medium ${
        known ? STATUS_STYLES[known] : "bg-stone-100 text-stone-700"
      }`}
    >
      {known ? STATUS_LABELS[known] : status}
    </span>
  );
}

// Bloom's users are at Salk; a fixed zone and locale render the same on the server and in the browser.
const TIME_FORMAT = new Intl.DateTimeFormat("en-US", {
  timeZone: "America/Los_Angeles",
  month: "short",
  day: "numeric",
  hour: "numeric",
  minute: "2-digit",
});

/** A timestamp as a short Pacific date and time, e.g. "Sep 29, 5:03 PM", or a dash. */
export function when(value: string | null): string {
  if (!value) return "—";
  return TIME_FORMAT.format(new Date(value));
}
