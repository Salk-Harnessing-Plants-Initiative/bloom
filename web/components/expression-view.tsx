"use client";

import { useCallback, useEffect, useState } from "react";
import Box from "@mui/material/Box";
import Typography from "@mui/material/Typography";

import { ExpressionUmap } from "@/components/expression-umap";
import { ExpressionPca3d } from "@/components/expression-pca3d";
import { ExpressionGeneSearch } from "@/components/expression-gene-search";
import { ExpressionColorbar } from "@/components/expression-colorbar";
import { ExpressionClusterSidebar } from "@/components/expression-cluster-sidebar";
import ToggleButtonGroup from "@mui/material/ToggleButtonGroup";
import ToggleButton from "@mui/material/ToggleButton";
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
  /** from scrna_cluster_stats.cell_count, keyed by ordinal */
  counts: Record<number, number>;
}

/**
 * Orchestrator for the new Expression UMAP view. Owns per-view state:
 * selected gene, cluster visibility, cluster highlight, colorbar range.
 * Composes the canvas + gene search + colorbar + sidebar.
 */
export function ExpressionView({ datasetId }: ExpressionViewProps) {
  const [meta, setMeta] = useState<LoadedMeta | null>(null);
  const [geneName, setGeneName] = useState<string | null>(null);
  const [hidden, setHidden] = useState<Set<number>>(new Set());
  const [highlighted, setHighlighted] = useState<number | null>(null);
  const [exprRange, setExprRange] = useState<{ min: number; max: number } | null>(
    null,
  );
  const [viewMode, setViewMode] = useState<"umap2d" | "pca3d">("umap2d");

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
    (ctx: { dataset: Dataset; clusters: Cluster[]; cellCount: number }) => {
      setMeta((prev) => ({
        dataset: ctx.dataset,
        clusters: ctx.clusters,
        cellCount: ctx.cellCount,
        counts: prev?.counts ?? {},
      }));
    },
    [],
  );

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
        highlightedOrdinal={highlighted}
        cellCounts={meta?.counts}
        onVisibilityChange={handleVisibilityChange}
        onHighlight={setHighlighted}
        onShowAll={handleShowAll}
        onHideAll={handleHideAll}
      />

      <Box sx={{ display: "flex", flexDirection: "column", gap: 2 }}>
        {/* dataset header */}
        {meta && (
          <Box sx={{ display: "flex", gap: 2, alignItems: "baseline" }}>
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

        {/* view mode + gene search */}
        <Box sx={{ display: "flex", gap: 2, alignItems: "center", flexWrap: "wrap" }}>
          <ToggleButtonGroup
            size="small"
            value={viewMode}
            exclusive
            onChange={(_e, v) => {
              if (v) setViewMode(v as "umap2d" | "pca3d");
            }}
            aria-label="View mode"
          >
            <ToggleButton value="umap2d">UMAP 2D</ToggleButton>
            <ToggleButton value="pca3d">PCA 3D</ToggleButton>
          </ToggleButtonGroup>
          {viewMode === "umap2d" && (
            <Box sx={{ maxWidth: 360, flex: 1 }}>
              <ExpressionGeneSearch
                datasetId={datasetId}
                value={geneName}
                onChange={setGeneName}
                disabled={!meta}
              />
            </Box>
          )}
        </Box>

        {/* canvas */}
        {viewMode === "umap2d" ? (
          <ExpressionUmap
            datasetId={datasetId}
            geneName={geneName}
            hiddenClusters={hidden}
            highlightedCluster={highlighted}
            onDataLoaded={handleDataLoaded}
            onExpressionRangeChanged={setExprRange}
          />
        ) : (
          <ExpressionPca3d
            datasetId={datasetId}
            hiddenClusters={hidden}
            highlightedCluster={highlighted}
            onDataLoaded={handleDataLoaded}
          />
        )}
      </Box>

      {/* colorbar: only when UMAP + gene is active */}
      <Box sx={{ width: 120, display: "flex", justifyContent: "flex-start", pt: 6 }}>
        {viewMode === "umap2d" && geneName && exprRange && (
          <ExpressionColorbar
            geneName={geneName}
            dataMin={exprRange.min}
            dataMax={exprRange.max}
            range={exprRange}
            onRangeChange={setExprRange}
            unitsLabel={unitsLabel}
          />
        )}
      </Box>
    </Box>
  );
}

export default ExpressionView;
