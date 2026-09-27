"use client";

interface Props {
  positive: number;
  total: number;
  /** What the total counts: "cells", or "cells that record it". */
  totalNoun: string;
  /** The groups holding the most transgene-positive cells, most first. */
  top: { name: string; positive: number }[];
}

/** A green bar that says how many cells carry the transgene and where most of them are. */
export function TransgeneSummary({ positive, total, totalNoun, top }: Props) {
  const fmt = new Intl.NumberFormat("en-US");
  const pct = total > 0 ? ((positive / total) * 100).toFixed(1) : "0.0";
  return (
    <div
      role="status"
      data-testid="transgene-summary"
      className="flex flex-wrap items-center gap-x-2 gap-y-1 rounded-lg border border-emerald-300 bg-emerald-50 px-3 py-2 text-sm text-emerald-950"
    >
      <span className="rounded-full bg-emerald-600 px-2.5 py-0.5 text-xs font-bold text-white">
        {fmt.format(positive)} transgene+
      </span>
      <span>
        of {fmt.format(total)} {totalNoun} ({pct}%)
      </span>
      {top.length > 0 && (
        <span>
          · most in{" "}
          {top.map((group, i) => (
            <span key={group.name}>
              {i > 0 ? ", " : ""}
              <strong className="font-semibold">{group.name}</strong> ({fmt.format(group.positive)})
            </span>
          ))}
        </span>
      )}
    </div>
  );
}

export default TransgeneSummary;
