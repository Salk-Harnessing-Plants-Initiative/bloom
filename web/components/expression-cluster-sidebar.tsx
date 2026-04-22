"use client";

import type { Database } from "@/lib/database.types";

type Cluster = Database["public"]["Tables"]["scrna_clusters"]["Row"];

export interface ExpressionClusterSidebarProps {
  clusters: Cluster[];
  hiddenOrdinals: ReadonlySet<number>;
  /** Per-cluster counts (from scrna_cluster_stats). Optional; falls back to null */
  cellCounts?: Record<number, number>;
  onVisibilityChange: (ordinal: number, visible: boolean) => void;
  /**
   * Solo a cluster: hide all others so only this one is visible. Clicking
   * a cluster that is already solo restores full visibility. Parent
   * updates the hidden-ordinals set to match, so checkboxes stay in sync
   * with what is actually rendered.
   */
  onSolo: (ordinal: number) => void;
  onShowAll: () => void;
  onHideAll: () => void;
}

/**
 * Vertical list of clusters styled as a quiet stat rail: colored bullet,
 * name, cell count, visibility indicator. Row click solos the cluster;
 * the right-edge dot toggles visibility without soloing.
 */
export function ExpressionClusterSidebar({
  clusters,
  hiddenOrdinals,
  cellCounts,
  onVisibilityChange,
  onSolo,
  onShowAll,
  onHideAll,
}: ExpressionClusterSidebarProps) {
  const soloCount = clusters.length - hiddenOrdinals.size;

  return (
    <div
      data-testid="expression-cluster-sidebar"
      className="w-72 h-full overflow-y-auto border-r border-stone-200 bg-white"
    >
      <div className="flex items-baseline justify-between px-4 pt-4 pb-2">
        <div className="text-xs uppercase tracking-widest text-stone-500">
          Clusters ({clusters.length})
        </div>
        <div className="flex gap-3 text-[11px]">
          <button
            type="button"
            onClick={onShowAll}
            className="text-lime-700 hover:underline"
          >
            Show all
          </button>
          <button
            type="button"
            onClick={onHideAll}
            className="text-stone-500 hover:underline"
          >
            Hide all
          </button>
        </div>
      </div>

      <ul className="pb-4">
        {clusters.map((c) => {
          const visible = !hiddenOrdinals.has(c.ordinal);
          const isSolo = soloCount === 1 && visible;
          const count = cellCounts?.[c.ordinal];
          const color = c.color ?? "#a8a29e";
          const label = c.name ?? c.cluster_id;

          return (
            <li key={c.ordinal}>
              <div
                className={[
                  "group flex items-center gap-3 px-4 py-2 cursor-pointer transition-colors",
                  isSolo
                    ? "bg-amber-50"
                    : "hover:bg-stone-50/70",
                ].join(" ")}
                onClick={() => onSolo(c.ordinal)}
                title="Click to show only this cluster (click again to restore)"
              >
                <span
                  aria-hidden
                  className="inline-block h-2.5 w-2.5 rounded-full shrink-0"
                  style={{ background: visible ? color : "transparent", border: visible ? "none" : `1px solid ${color}` }}
                />
                <span
                  className={[
                    "flex-1 truncate text-sm",
                    isSolo
                      ? "text-stone-900 font-medium"
                      : visible
                        ? "text-stone-700"
                        : "text-stone-400",
                  ].join(" ")}
                  title={label}
                >
                  {label}
                </span>
                {count != null ? (
                  <span
                    className={[
                      "text-xs tabular-nums shrink-0",
                      isSolo
                        ? "text-stone-700"
                        : visible
                          ? "text-stone-500"
                          : "text-stone-300",
                    ].join(" ")}
                  >
                    {count.toLocaleString()}
                  </span>
                ) : null}
                <button
                  type="button"
                  onClick={(e) => {
                    e.stopPropagation();
                    onVisibilityChange(c.ordinal, !visible);
                  }}
                  aria-label={visible ? "Hide cluster" : "Show cluster"}
                  className="shrink-0 p-1 -mr-1 rounded-full hover:bg-stone-100"
                >
                  <span
                    className={[
                      "block h-2 w-2 rounded-full transition-all",
                      visible
                        ? "bg-lime-500 shadow-[0_0_6px_rgba(132,204,22,0.75)]"
                        : "bg-transparent border border-stone-300",
                    ].join(" ")}
                  />
                </button>
              </div>
            </li>
          );
        })}
      </ul>
    </div>
  );
}

export default ExpressionClusterSidebar;
