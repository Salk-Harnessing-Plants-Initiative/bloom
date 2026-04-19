"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import createREGL, { type Regl } from "regl";

import {
  fetchCells,
  fetchClusters,
  fetchDataset,
  fetchGeneBin,
  type CellArraysRow,
} from "@/components/expression-lib/scrna-client";
import {
  CLUSTER_FRAG,
  EXPRESSION_FRAG,
  EXPRESSION_VERT,
  POINT_VERT,
} from "@/components/expression-lib/shaders";
import { VIRIDIS_STOPS, viridisUniformArray } from "@/components/expression-lib/viridis";
import type { Database } from "@/lib/database.types";

type Dataset = Database["public"]["Tables"]["scrna_datasets"]["Row"];
type Cluster = Database["public"]["Tables"]["scrna_clusters"]["Row"];

const DEFAULT_POINT_SIZE = 4.0;
const NORMALIZE_PADDING = 1.05; // give a small border around the UMAP bbox

export interface ExpressionUmapProps {
  datasetId: number;
  /** Currently-selected gene for expression overlay; null = color by cluster */
  geneName?: string | null;
  /** Clusters currently hidden (ordinal set). Empty = all visible. */
  hiddenClusters?: ReadonlySet<number>;
  /** Cluster to highlight; others dim. null = no highlight */
  highlightedCluster?: number | null;
  /** Height of the canvas in pixels; width fills the parent */
  height?: number;
  /** Fires when data is loaded so parent can render colorbar / sidebar */
  onDataLoaded?: (ctx: {
    dataset: Dataset;
    clusters: Cluster[];
    cellCount: number;
  }) => void;
  /** Fires whenever the currently-overlaid gene's min/max changes */
  onExpressionRangeChanged?: (range: { min: number; max: number } | null) => void;
}

interface LoadedData {
  dataset: Dataset;
  clusters: Cluster[];
  cells: CellArraysRow[];
  /** positions packed as [x0, y0, x1, y1, ...] */
  positions: Float32Array;
  /** rgba colors packed as [r0, g0, b0, a0, ...] from cluster palette */
  clusterColors: Float32Array;
  /** length N, 1.0 visible / 0.0 hidden */
  visibility: Float32Array;
  /** per-cell cluster ordinal, kept for recomputing visibility */
  clusterOrdinals: Uint8Array;
  /** normalization: maps dataset coord → [-1, 1] NDC region */
  normScale: number;
  normCenterX: number;
  normCenterY: number;
}

/**
 * Hex color string like "#4d7c0f" → normalized [r, g, b] components in [0, 1].
 * Unknown/missing colors fall back to gray so the shader still renders.
 */
function hexToRgb(hex: string | null): [number, number, number] {
  if (!hex) return [0.5, 0.5, 0.5];
  const trimmed = hex.replace(/^#/, "");
  if (trimmed.length !== 6) return [0.5, 0.5, 0.5];
  const n = parseInt(trimmed, 16);
  if (Number.isNaN(n)) return [0.5, 0.5, 0.5];
  return [((n >> 16) & 0xff) / 255, ((n >> 8) & 0xff) / 255, (n & 0xff) / 255];
}

/** Build a Float32Array of packed rgba colors, one per cell, from the cluster palette. */
function packClusterColors(
  cells: CellArraysRow[],
  clusters: Cluster[],
): Float32Array {
  const paletteRgb = new Map<number, [number, number, number]>();
  for (const c of clusters) {
    paletteRgb.set(c.ordinal, hexToRgb(c.color));
  }
  const out = new Float32Array(cells.length * 4);
  for (let i = 0; i < cells.length; i++) {
    const rgb = paletteRgb.get(cells[i].cluster_ordinal) ?? [0.5, 0.5, 0.5];
    out[i * 4] = rgb[0];
    out[i * 4 + 1] = rgb[1];
    out[i * 4 + 2] = rgb[2];
    out[i * 4 + 3] = 1.0;
  }
  return out;
}

/** Build Float32Array positions and compute normalization so coords map to ~[-1, 1]. */
function packPositions(cells: CellArraysRow[]): {
  positions: Float32Array;
  normScale: number;
  normCenterX: number;
  normCenterY: number;
} {
  const n = cells.length;
  const positions = new Float32Array(n * 2);
  let minX = Infinity;
  let maxX = -Infinity;
  let minY = Infinity;
  let maxY = -Infinity;
  for (let i = 0; i < n; i++) {
    positions[i * 2] = cells[i].x;
    positions[i * 2 + 1] = cells[i].y;
    if (cells[i].x < minX) minX = cells[i].x;
    if (cells[i].x > maxX) maxX = cells[i].x;
    if (cells[i].y < minY) minY = cells[i].y;
    if (cells[i].y > maxY) maxY = cells[i].y;
  }
  const centerX = (minX + maxX) / 2;
  const centerY = (minY + maxY) / 2;
  const rangeX = maxX - minX;
  const rangeY = maxY - minY;
  const range = Math.max(rangeX, rangeY) || 1;
  const scale = 2 / (range * NORMALIZE_PADDING);
  // pre-center positions in-place so the shader only needs to scale + translate
  for (let i = 0; i < n; i++) {
    positions[i * 2] = (positions[i * 2] - centerX) * scale;
    positions[i * 2 + 1] = (positions[i * 2 + 1] - centerY) * scale;
  }
  return { positions, normScale: scale, normCenterX: centerX, normCenterY: centerY };
}

export function ExpressionUmap({
  datasetId,
  geneName,
  hiddenClusters,
  highlightedCluster,
  height = 600,
  onDataLoaded,
  onExpressionRangeChanged,
}: ExpressionUmapProps) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const reglRef = useRef<Regl | null>(null);
  const [data, setData] = useState<LoadedData | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [expressionArr, setExpressionArr] = useState<Float32Array | null>(null);
  const [expressionRange, setExpressionRange] = useState<{
    min: number;
    max: number;
  } | null>(null);

  const [zoom, setZoom] = useState(1);
  const [translate, setTranslate] = useState<[number, number]>([0, 0]);

  // -------- data load ---------------------------------------------------------
  useEffect(() => {
    let cancelled = false;
    setLoadError(null);
    setData(null);

    (async () => {
      try {
        const [dataset, clusters, cells] = await Promise.all([
          fetchDataset(datasetId),
          fetchClusters(datasetId),
          fetchCells(datasetId),
        ]);
        if (cancelled) return;
        if (!dataset) {
          setLoadError(`Dataset ${datasetId} not found.`);
          return;
        }
        const { positions, normScale, normCenterX, normCenterY } = packPositions(cells);
        const clusterColors = packClusterColors(cells, clusters);
        const visibility = new Float32Array(cells.length);
        visibility.fill(1.0);
        const clusterOrdinals = new Uint8Array(cells.length);
        for (let i = 0; i < cells.length; i++) {
          clusterOrdinals[i] = cells[i].cluster_ordinal;
        }
        const loaded: LoadedData = {
          dataset,
          clusters,
          cells,
          positions,
          clusterColors,
          visibility,
          clusterOrdinals,
          normScale,
          normCenterX,
          normCenterY,
        };
        setData(loaded);
        onDataLoaded?.({ dataset, clusters, cellCount: cells.length });
      } catch (err) {
        if (!cancelled) {
          setLoadError(err instanceof Error ? err.message : String(err));
        }
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [datasetId, onDataLoaded]);

  // -------- gene overlay fetch -----------------------------------------------
  useEffect(() => {
    if (!data || !geneName) {
      setExpressionArr(null);
      setExpressionRange(null);
      onExpressionRangeChanged?.(null);
      return;
    }
    let cancelled = false;
    (async () => {
      try {
        const arr = await fetchGeneBin(data.dataset.name, geneName);
        if (cancelled) return;
        let min = Infinity;
        let max = -Infinity;
        for (let i = 0; i < arr.length; i++) {
          if (arr[i] < min) min = arr[i];
          if (arr[i] > max) max = arr[i];
        }
        if (!Number.isFinite(min)) min = 0;
        if (!Number.isFinite(max)) max = 0;
        setExpressionArr(arr);
        const range = { min, max };
        setExpressionRange(range);
        onExpressionRangeChanged?.(range);
      } catch (err) {
        if (!cancelled) {
          setExpressionArr(null);
          setExpressionRange(null);
          onExpressionRangeChanged?.(null);
          console.error("[ExpressionUmap] gene bin fetch failed:", err);
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [data, geneName, onExpressionRangeChanged]);

  // -------- visibility recompute from hidden / highlighted -------------------
  const visibility = useMemo(() => {
    if (!data) return null;
    const out = new Float32Array(data.cells.length);
    const hidden = hiddenClusters ?? new Set<number>();
    for (let i = 0; i < data.cells.length; i++) {
      const ord = data.clusterOrdinals[i];
      if (hidden.has(ord)) {
        out[i] = 0;
      } else if (
        highlightedCluster != null &&
        ord !== highlightedCluster
      ) {
        out[i] = 0.25; // dimmed, not hidden
      } else {
        out[i] = 1.0;
      }
    }
    return out;
  }, [data, hiddenClusters, highlightedCluster]);

  // -------- regl init + render loop ------------------------------------------
  useEffect(() => {
    if (!data || !canvasRef.current) return;
    const canvas = canvasRef.current;
    const regl = createREGL({
      canvas,
      attributes: { antialias: true, preserveDrawingBuffer: false },
    });
    reglRef.current = regl;

    const positionBuffer = regl.buffer(data.positions);
    const colorBuffer = regl.buffer(data.clusterColors);
    const visibilityBuffer = regl.buffer(visibility ?? data.visibility);
    const expressionBuffer = regl.buffer(
      expressionArr ?? new Float32Array(data.cells.length),
    );

    const viridisFlat = viridisUniformArray();
    const viridisUniforms: Record<string, [number, number, number]> = {};
    for (let i = 0; i < VIRIDIS_STOPS.length; i++) {
      viridisUniforms[`viridis[${i}]`] = [
        viridisFlat[i * 3],
        viridisFlat[i * 3 + 1],
        viridisFlat[i * 3 + 2],
      ];
    }

    const drawClusters = regl({
      vert: POINT_VERT,
      frag: CLUSTER_FRAG,
      attributes: {
        position: positionBuffer,
        color: colorBuffer,
        visible: visibilityBuffer,
      },
      uniforms: {
        zoom: regl.prop<{ zoom: number }, "zoom">("zoom"),
        translate: regl.prop<{ translate: [number, number] }, "translate">("translate"),
        pointSize: DEFAULT_POINT_SIZE,
      },
      count: data.cells.length,
      primitive: "points",
      blend: {
        enable: true,
        func: { src: "src alpha", dst: "one minus src alpha" },
      },
      depth: { enable: false },
    });

    const drawExpression = regl({
      vert: EXPRESSION_VERT,
      frag: EXPRESSION_FRAG,
      attributes: {
        position: positionBuffer,
        expression: expressionBuffer,
        visible: visibilityBuffer,
      },
      uniforms: {
        zoom: regl.prop<{ zoom: number }, "zoom">("zoom"),
        translate: regl.prop<{ translate: [number, number] }, "translate">("translate"),
        pointSize: DEFAULT_POINT_SIZE,
        expMin: regl.prop<{ expMin: number }, "expMin">("expMin"),
        expMax: regl.prop<{ expMax: number }, "expMax">("expMax"),
        ...viridisUniforms,
      },
      count: data.cells.length,
      primitive: "points",
      blend: {
        enable: true,
        func: { src: "src alpha", dst: "one minus src alpha" },
      },
      depth: { enable: false },
    });

    let rafId = 0;
    const tick = () => {
      regl.poll();
      regl.clear({ color: [0.05, 0.05, 0.08, 1], depth: 1 });
      if (expressionArr && expressionRange) {
        drawExpression({
          zoom,
          translate,
          expMin: expressionRange.min,
          expMax: expressionRange.max,
        });
      } else {
        drawClusters({ zoom, translate });
      }
      rafId = requestAnimationFrame(tick);
    };
    rafId = requestAnimationFrame(tick);

    return () => {
      cancelAnimationFrame(rafId);
      positionBuffer.destroy();
      colorBuffer.destroy();
      visibilityBuffer.destroy();
      expressionBuffer.destroy();
      regl.destroy();
      reglRef.current = null;
    };
  }, [data, visibility, expressionArr, expressionRange, zoom, translate]);

  // -------- zoom / pan handlers ----------------------------------------------
  const handleWheel = useCallback((e: React.WheelEvent<HTMLCanvasElement>) => {
    e.preventDefault();
    const factor = Math.exp(-e.deltaY * 0.001);
    setZoom((z) => Math.min(50, Math.max(0.2, z * factor)));
  }, []);

  const dragState = useRef<{ x: number; y: number; origTx: number; origTy: number } | null>(
    null,
  );
  const handlePointerDown = useCallback(
    (e: React.PointerEvent<HTMLCanvasElement>) => {
      e.currentTarget.setPointerCapture(e.pointerId);
      dragState.current = {
        x: e.clientX,
        y: e.clientY,
        origTx: translate[0],
        origTy: translate[1],
      };
    },
    [translate],
  );
  const handlePointerMove = useCallback(
    (e: React.PointerEvent<HTMLCanvasElement>) => {
      if (!dragState.current || !canvasRef.current) return;
      const rect = canvasRef.current.getBoundingClientRect();
      // Convert pixel delta to NDC delta, divide by zoom so drag feels 1:1 on screen
      const dx = ((e.clientX - dragState.current.x) / rect.width) * 2;
      const dy = -((e.clientY - dragState.current.y) / rect.height) * 2;
      setTranslate([
        dragState.current.origTx + dx / zoom,
        dragState.current.origTy + dy / zoom,
      ]);
    },
    [zoom],
  );
  const handlePointerUp = useCallback(
    (e: React.PointerEvent<HTMLCanvasElement>) => {
      e.currentTarget.releasePointerCapture(e.pointerId);
      dragState.current = null;
    },
    [],
  );

  // -------- render -----------------------------------------------------------
  if (loadError) {
    return (
      <div
        role="alert"
        style={{
          padding: 24,
          color: "#f43f5e",
          background: "#18181b",
          borderRadius: 8,
          fontFamily: "monospace",
        }}
      >
        Failed to load UMAP: {loadError}
      </div>
    );
  }
  if (!data) {
    return (
      <div
        style={{
          height,
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          background: "#18181b",
          color: "#a1a1aa",
          borderRadius: 8,
        }}
      >
        Loading UMAP…
      </div>
    );
  }

  return (
    <canvas
      ref={canvasRef}
      data-testid="expression-umap-canvas"
      width={1200}
      height={height}
      onWheel={handleWheel}
      onPointerDown={handlePointerDown}
      onPointerMove={handlePointerMove}
      onPointerUp={handlePointerUp}
      style={{
        width: "100%",
        height,
        display: "block",
        borderRadius: 8,
        cursor: dragState.current ? "grabbing" : "grab",
        touchAction: "none",
      }}
    />
  );
}

export default ExpressionUmap;
