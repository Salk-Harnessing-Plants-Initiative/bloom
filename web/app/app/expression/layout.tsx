import type { ReactNode } from "react";

/**
 * Shared shell for every route under /app/expression.
 *
 * Guarantees the same centered content column across:
 *   - /app/expression                        (species list)
 *   - /app/expression/[speciesId]            (species detail + dataset list)
 *   - /app/expression/[speciesId]/[datasetId] (dataset cockpit)
 *
 * Previously each page set its own `max-w-*` so the left edge shifted
 * when navigating between them. A Next.js route-level layout is the
 * right primitive for this kind of invariant: every child page inherits
 * the same container without having to remember to set it themselves.
 */
export default function ExpressionLayout({
  children,
}: {
  children: ReactNode;
}) {
  return <div className="max-w-7xl mx-auto px-4">{children}</div>;
}
