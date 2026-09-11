"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import Box from "@mui/material/Box";
import Typography from "@mui/material/Typography";

import { ExpressionUmap } from "@/components/expression-umap";
import {
  countsFor,
  SAMPLE_FILTER,
  countFocused,
  describeFocus,
  focusIsSet,
} from "@/components/expression-lib/umap-packing";
import type { CellArraysRow } from "@/components/expression-lib/scrna-client";
import { ExpressionSampleToggles } from "./expression-sample-toggles";
// Gene search disabled — see the JSX comment below.
// import { ExpressionGeneSearch } from "@/components/expression-gene-search";
import { ExpressionColorbar } from "@/components/expression-colorbar";
import { ExpressionClusterSidebar } from "@/components/expression-cluster-sidebar";
import { ExpressionClusterDetailPanel } from "@/components/expression-cluster-detail-panel";
import { TransgeneSummary } from "@/components/transgene-summary";
import { topGroups, transgeneByCluster } from "@/components/expression-lib/transgene";
import { createClientSupabaseClient } from "@/lib/supabase/client";
import type { Database } from "@/lib/database.types";

type Cluster = Database["public"]["Tables"]["scrna_clusters"]["Row"];
type Dataset = Database["public"]["Tables"]["scrna_datasets"]["Row"];

const DEFAULT_UNITS_FALLBACK = "log-normalized";

export interface ExpressionViewProps {
  datasetId: number;
  datasetName?: string;
}

interface LoadedMeta {
  dataset: Dataset;
  clusters: Cluster[];
  cellCount: number;
  /** Cells whose `cluster_id` had no row in `scrna_clusters` (sentinel ordinal 255). */
  orphanCount: number;
  /** from scrna_cluster_stats.cell_count, keyed by ordinal */
  counts: Record<number, number>;
  /** The filter rows to show, the sample row first. Empty means no toggles. */
  filters: string[];
  /** Per filter row, the cells with no value for it. */
  unlabelled: Record<string, number>;
  /** The cells, so each row counts what the other rows leave showing. */
  cells: Pick<CellArraysRow, "replicate" | "facets" | "cluster_ordinal">[];
}

const NOTHING_HIDDEN: ReadonlySet<string> = new Set();
const NOTHING_FOCUSED: ReadonlySet<string> = new Set();

/** Composes the UMAP canvas + gene search + colorbar + cluster sidebar for a dataset. */
export function ExpressionView({ datasetId }: ExpressionViewProps) {
  const [meta, setMeta] = useState<LoadedMeta | null>(null);
  const [geneName, setGeneName] = useState<string | null>(null);
  const [hidden, setHidden] = useState<Set<number>>(new Set());
  // Hidden values per filter row: the sample row and each label.
  const [hiddenValues, setHiddenValues] = useState<Map<string, Set<string>>>(
    new Map(),
  );
  // Values focused on per filter row; every cell outside the focus is greyed out.
  const [focusedValues, setFocusedValues] = useState<Map<string, Set<string>>>(
    new Map(),
  );
  // Sample names repeat across datasets -- Col-0 is in most of them -- so a
  // hidden set carried over would open the next dataset with one already off.
  useEffect(() => {
    setHiddenValues(new Map());
    setFocusedValues(new Map());
    // The chips come from `meta`. Left alone it still describes the previous
    // dataset for the whole of this one's fetch, and a click in that window
    // writes a name the new dataset may not have into the hidden set.
    setMeta(null);
  }, [datasetId]);
  const [exprRange, setExprRange] = useState<{ min: number; max: number } | null>(
    null,
  );

  useEffect(() => {
    // load cluster counts from scrna_cluster_stats alongside the
    // datasets/clusters that the UMAP fetches independently. We keep
    // this separate so the UMAP's first paint doesn't wait on stats.
    let cancelled = false;
    (async () => {
      const supabase = createClientSupabaseClient();
      const { data, error } = await supabase
        .from("scrna_cluster_stats")
        .select("cluster_id,cell_count")
        .eq("dataset_id", datasetId);
      if (cancelled || error || !data) return;
      // map cluster_id (text) → cell_count; ordinal mapping happens on
      // handleDataLoaded when we know the cluster catalog.
      const byClusterText: Record<string, number> = {};
      for (const row of data) {
        byClusterText[row.cluster_id] = row.cell_count;
      }
      setMeta((prev) => {
        if (!prev) return prev;
        const counts: Record<number, number> = {};
        for (const c of prev.clusters) {
          const n = byClusterText[c.cluster_id];
          if (typeof n === "number") counts[c.ordinal] = n;
        }
        return { ...prev, counts };
      });
    })();
    return () => {
      cancelled = true;
    };
  }, [datasetId]);

  const handleDataLoaded = useCallback(
    (ctx: {
      dataset: Dataset;
      clusters: Cluster[];
      cellCount: number;
      orphanCount: number;
      filters: string[];
      unlabelled: Record<string, number>;
      cells: Pick<CellArraysRow, "replicate" | "facets" | "cluster_ordinal">[];
    }) => {
      setMeta((prev) => ({
        dataset: ctx.dataset,
        clusters: ctx.clusters,
        cellCount: ctx.cellCount,
        orphanCount: ctx.orphanCount,
        filters: ctx.filters,
        unlabelled: ctx.unlabelled,
        cells: ctx.cells,
        counts: prev?.counts ?? {},
      }));
    },
    [],
  );

  const handleFilterToggle = useCallback((filter: string, value: string) => {
    setHiddenValues((prev) => {
      const next = new Map(prev);
      const hidden = new Set(next.get(filter) ?? []);
      if (!hidden.delete(value)) hidden.add(value);
      next.set(filter, hidden);
      return next;
    });
  }, []);

  const handleShowAllOf = useCallback((filter: string) => {
    setHiddenValues((prev) => {
      const next = new Map(prev);
      next.set(filter, new Set());
      return next;
    });
  }, []);

  const handleFocusToggle = useCallback((filter: string, value: string) => {
    setFocusedValues((prev) => {
      const next = new Map(prev);
      const values = new Set(next.get(filter) ?? []);
      if (!values.delete(value)) values.add(value);
      next.set(filter, values);
      return next;
    });
  }, []);

  const anyValueHidden = [...hiddenValues.values()].some((s) => s.size > 0);
  const focusSet = focusIsSet(focusedValues);
  const focusedCount = meta && focusSet ? countFocused(meta.cells, focusedValues, hiddenValues) : 0;

  // Transgene-positive cells per cluster, over the whole dataset.
  const transgene = useMemo(() => (meta ? transgeneByCluster(meta.cells) : null), [meta]);
  const transgeneTotal = transgene
    ? [...transgene.values()].reduce((sum, t) => sum + t.positive, 0)
    : 0;

  const handleVisibilityChange = useCallback(
    (ordinal: number, visible: boolean) => {
      setHidden((prev) => {
        const next = new Set(prev);
        if (visible) next.delete(ordinal);
        else next.add(ordinal);
        return next;
      });
    },
    [],
  );

  const handleShowAll = useCallback(() => setHidden(new Set()), []);
  const handleHideAll = useCallback(() => {
    if (!meta) return;
    setHidden(new Set(meta.clusters.map((c) => c.ordinal)));
  }, [meta]);

  /** Hide all clusters except this one. Clicking again restores full visibility. */
  const handleSolo = useCallback(
    (ordinal: number) => {
      if (!meta) return;
      const allOrdinals = meta.clusters.map((c) => c.ordinal);
      const visible = allOrdinals.filter((o) => !hidden.has(o));
      const alreadySolo = visible.length === 1 && visible[0] === ordinal;
      if (alreadySolo) {
        setHidden(new Set());
      } else {
        setHidden(new Set(allOrdinals.filter((o) => o !== ordinal)));
      }
    },
    [meta, hidden],
  );

  const datasetName = meta?.dataset.name;
  const unitsLabel = meta?.dataset.expression_units ?? DEFAULT_UNITS_FALLBACK;

  return (
    <Box
      sx={{
        display: "grid",
        gridTemplateColumns: "auto 1fr auto",
        gap: 2,
        alignItems: "stretch",
      }}
    >
      <ExpressionClusterSidebar
        clusters={meta?.clusters ?? []}
        hiddenOrdinals={hidden}
        cellCounts={meta?.counts}
        transgene={transgene ?? undefined}
        onVisibilityChange={handleVisibilityChange}
        onSolo={handleSolo}
        onShowAll={handleShowAll}
        onHideAll={handleHideAll}
      />

      <Box sx={{ display: "flex", flexDirection: "column", gap: 2, minWidth: 0 }}>
        {/* dataset header */}
        {meta && (
          <Box sx={{ display: "flex", gap: 2, alignItems: "baseline", minWidth: 0, flexWrap: "wrap" }}>
            <Typography variant="h6" sx={{ fontFamily: "monospace" }}>
              {meta.dataset.name}
            </Typography>
            <Typography variant="caption" color="text.secondary">
              {meta.dataset.n_cells?.toLocaleString() ?? meta.cellCount.toLocaleString()}
              {" cells · "}
              {meta.dataset.n_genes?.toLocaleString() ?? "?"} genes
              {meta.dataset.assembly ? ` · ${meta.dataset.assembly}` : ""}
              {meta.dataset.annotation ? ` · ${meta.dataset.annotation}` : ""}
            </Typography>
          </Box>
        )}

        {/* Gene search disabled — re-enable by uncommenting the Box below
            and restoring ExpressionGeneSearch + colorbar wiring. The
            geneName state is kept so UMAP/colorbar code paths stay typed. */}
        {(() => {
          const clusters = meta?.clusters ?? [];
          if (clusters.length === 0) return null;
          const soloCount = clusters.length - hidden.size;
          if (soloCount === 1) return null;
          return (
            <span
              className="inline-flex items-center gap-2 self-start rounded-full border border-lime-200 bg-lime-50/70 px-3 py-1 text-xs text-lime-800"
              role="status"
            >
              <span className="inline-block h-1.5 w-1.5 rounded-full bg-lime-500 shadow-[0_0_4px_rgba(132,204,22,0.8)]" />
              Click a cluster for details
            </span>
          );
        })()}

        {/* orphan-cell warning */}
        {meta && meta.orphanCount > 0 && (
          <span
            className="inline-flex items-center gap-2 self-start rounded-full border border-amber-200 bg-amber-50/70 px-3 py-1 text-xs text-amber-900"
            role="status"
          >
            <span className="inline-block h-1.5 w-1.5 rounded-full bg-amber-500" />
            {meta.orphanCount.toLocaleString()} cell
            {meta.orphanCount === 1 ? "" : "s"} (
            {((meta.orphanCount / meta.cellCount) * 100).toFixed(1)}%) have no
            cluster assignment — shown in gray
          </span>
        )}

        {meta && transgene && transgeneTotal > 0 && (
          <TransgeneSummary
            positive={transgeneTotal}
            total={meta.cellCount}
            totalNoun="cells"
            top={topGroups(
              meta.clusters.map((c) => ({
                name: c.name || c.cluster_id,
                positive: transgene.get(c.ordinal)?.positive ?? 0,
              })),
            )}
          />
        )}

        {meta && meta.filters.length > 0 && (
          <Box sx={{ pb: 1, display: "flex", flexDirection: "column", gap: 1 }}>
            {meta.filters.map((filter) => (
              <ExpressionSampleToggles
                key={filter}
                label={filter === SAMPLE_FILTER ? "Samples" : filter}
                noun={filter === SAMPLE_FILTER ? "sample" : `${filter} value`}
                samples={countsFor(meta.cells, hiddenValues, filter)}
                hidden={hiddenValues.get(filter) ?? NOTHING_HIDDEN}
                unlabelledCount={meta.unlabelled[filter] ?? 0}
                onToggle={(value) => handleFilterToggle(filter, value)}
                onShowAll={() => handleShowAllOf(filter)}
                focused={focusedValues.get(filter) ?? NOTHING_FOCUSED}
                onFocus={(value) => handleFocusToggle(filter, value)}
              />
            ))}
            {anyValueHidden && (
              <span className="block text-xs text-stone-500" role="status">
                Cluster sizes, marker genes and the no-cluster figure are for
                the whole dataset, not only the cells shown.
              </span>
            )}
            {focusSet && (
              <span className="block text-xs text-stone-500" role="status">
                {focusedCount.toLocaleString()} {focusedCount === 1 ? "cell is" : "cells are"}{" "}
                {describeFocus(focusedValues)}; every other cell is greyed out.{" "}
                <button
                  type="button"
                  onClick={() => setFocusedValues(new Map())}
                  className="underline hover:text-stone-700"
                >
                  Clear focus
                </button>
              </span>
            )}
          </Box>
        )}

        {/* canvas */}
        <ExpressionUmap
          datasetId={datasetId}
          geneName={geneName}
          hiddenClusters={hidden}
          hiddenValues={hiddenValues}
          focusedValues={focusedValues}
          onDataLoaded={handleDataLoaded}
          onExpressionRangeChanged={setExprRange}
          onCellClick={handleSolo}
        />
      </Box>

      <Box sx={{ display: "flex", flexDirection: "column", gap: 2, pt: 2 }}>
        {geneName && exprRange && (
          <Box sx={{ width: 120 }}>
            <ExpressionColorbar
              geneName={geneName}
              dataMin={exprRange.min}
              dataMax={exprRange.max}
              range={exprRange}
              onRangeChange={setExprRange}
              unitsLabel={unitsLabel}
            />
          </Box>
        )}
        {(() => {
          const clusters = meta?.clusters ?? [];
          if (clusters.length === 0) return null;
          const soloCount = clusters.length - hidden.size;
          if (soloCount !== 1) return null;
          const soloCluster = clusters.find((c) => !hidden.has(c.ordinal));
          if (!soloCluster) return null;
          return (
            <ExpressionClusterDetailPanel
              datasetId={datasetId}
              clusterId={soloCluster.cluster_id}
              clusterName={soloCluster.name}
              clusterColor={soloCluster.color}
              transgene={transgene?.get(soloCluster.ordinal)}
            />
          );
        })()}
      </Box>
    </Box>
  );
}

export default ExpressionView;
