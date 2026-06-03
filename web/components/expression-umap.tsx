"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import createREGL, { type Regl, type Buffer as ReglBuffer, type DrawCommand } from "regl";

import {
  fetchCells,
  fetchClusters,
  fetchDataset,
  fetchGeneBin,
  ORPHAN_CLUSTER_ORDINAL,
  type CellArraysRow,
} from "@/components/expression-lib/scrna-client";
import {
  CLUSTER_FRAG,
  EXPRESSION_FRAG,
  EXPRESSION_VERT,
  POINT_VERT,
} from "@/components/expression-lib/shaders";
import type { Database } from "@/lib/database.types";

type Dataset = Database["public"]["Tables"]["scrna_datasets"]["Row"];
type Cluster = Database["public"]["Tables"]["scrna_clusters"]["Row"];

const DEFAULT_POINT_SIZE = 4.0;
const NORMALIZE_PADDING = 1.05; // give a small border around the UMAP bbox

const ORPHAN_GRAY: [number, number, number] = [0.6, 0.6, 0.6];

export interface ExpressionUmapProps {
  datasetId: number;
  /** Currently-selected gene for expression overlay; null = color by cluster */
  geneName?: string | null;
  /** Clusters currently hidden (ordinal set). Empty = all visible. */
  hiddenClusters?: ReadonlySet<number>;
  /** Height of the canvas in pixels; width fills the parent */
  height?: number;
  /** Fires when data is loaded so parent can render colorbar / sidebar */
  onDataLoaded?: (ctx: {
    dataset: Dataset;
    clusters: Cluster[];
    cellCount: number;
    /** Cells whose `cluster_id` had no row in `scrna_clusters` (sentinel ordinal 255). */
    orphanCount: number;
  }) => void;
  /** Fires whenever the currently-overlaid gene's min/max changes */
  onExpressionRangeChanged?: (range: { min: number; max: number } | null) => void;
}

interface LoadedData {
  dataset: Dataset;
  clusters: Cluster[];
  cells: CellArraysRow[];
  positions: Float32Array;
  clusterColors: Float32Array;
  visibility: Float32Array;
  clusterOrdinals: Uint8Array;
  normScale: number;
  normCenterX: number;
  normCenterY: number;
}

function hexToRgb(hex: string | null): [number, number, number] {
  if (!hex) return [0.5, 0.5, 0.5];
  const trimmed = hex.replace(/^#/, "");
  if (trimmed.length !== 6) return [0.5, 0.5, 0.5];
  const n = parseInt(trimmed, 16);
  if (Number.isNaN(n)) return [0.5, 0.5, 0.5];
  return [((n >> 16) & 0xff) / 255, ((n >> 8) & 0xff) / 255, (n & 0xff) / 255];
}

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
    const ord = cells[i].cluster_ordinal;
    const rgb =
      ord === ORPHAN_CLUSTER_ORDINAL
        ? ORPHAN_GRAY
        : paletteRgb.get(ord) ?? ORPHAN_GRAY;
    out[i * 4] = rgb[0];
    out[i * 4 + 1] = rgb[1];
    out[i * 4 + 2] = rgb[2];
    out[i * 4 + 3] = 1.0;
  }
  return out;
}

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
  height = 600,
  onDataLoaded,
  onExpressionRangeChanged,
}: ExpressionUmapProps) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);

  // The init effect creates the regl context once per dataset and stores
  // GPU handles in refs. Camera, visibility, and expression updates write
  // to existing buffers via `subdata` — they never re-create the context.
  const reglRef = useRef<Regl | null>(null);
  const positionBufferRef = useRef<ReglBuffer | null>(null);
  const colorBufferRef = useRef<ReglBuffer | null>(null);
  const visibilityBufferRef = useRef<ReglBuffer | null>(null);
  const expressionBufferRef = useRef<ReglBuffer | null>(null);
  const drawClustersRef = useRef<DrawCommand | null>(null);
  const drawExpressionRef = useRef<DrawCommand | null>(null);
  /**
   * Length the GPU buffers were allocated for. Subdata writes from the
   * [visibility] / [expressionArr] effects skip when array length doesn't
   * match this — guards against writing into a buffer that's mid-resize
   * during a dataset switch.
   */
  const cellCountRef = useRef<number>(0);

  const [data, setData] = useState<LoadedData | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [webglError, setWebglError] = useState<string | null>(null);
  const [expressionArr, setExpressionArr] = useState<Float32Array | null>(null);
  const [expressionRange, setExpressionRange] = useState<{
    min: number;
    max: number;
  } | null>(null);

  const [zoom, setZoom] = useState(1);
  const [translate, setTranslate] = useState<[number, number]>([0, 0]);

  const zoomRef = useRef(zoom);
  const translateRef = useRef(translate);
  const expressionArrRef = useRef(expressionArr);
  const expressionRangeRef = useRef(expressionRange);
  const dirtyRef = useRef(true);
  useEffect(() => {
    zoomRef.current = zoom;
    translateRef.current = translate;
    expressionArrRef.current = expressionArr;
    expressionRangeRef.current = expressionRange;
    dirtyRef.current = true;
  });

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
        let orphanCount = 0;
        for (let i = 0; i < cells.length; i++) {
          clusterOrdinals[i] = cells[i].cluster_ordinal;
          if (cells[i].cluster_ordinal === ORPHAN_CLUSTER_ORDINAL) orphanCount++;
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
        onDataLoaded?.({
          dataset,
          clusters,
          cellCount: cells.length,
          orphanCount,
        });
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

  // -------- visibility recompute from hidden set -----------------------------
  const visibility = useMemo(() => {
    if (!data) return null;
    const out = new Float32Array(data.cells.length);
    const hidden = hiddenClusters ?? new Set<number>();
    for (let i = 0; i < data.cells.length; i++) {
      const ord = data.clusterOrdinals[i];
      out[i] = hidden.has(ord) ? 0 : 1.0;
    }
    return out;
  }, [data, hiddenClusters]);

  // -------- regl init + render loop (runs ONCE per dataset) ------------------
  useEffect(() => {
    if (!data || !canvasRef.current) return;
    const canvas = canvasRef.current;
    setWebglError(null);

    let regl: Regl;
    try {
      regl = createREGL({
        canvas,
        attributes: { antialias: true, preserveDrawingBuffer: false },
        // regl reports context-creation failure via onDone, not by throwing.
        onDone: (err) => {
          if (err) {
            setWebglError(
              "WebGL is required for the UMAP visualization. Please enable WebGL or update your browser.",
            );
          }
        },
      });
    } catch (err) {
      setWebglError(
        err instanceof Error
          ? `WebGL setup failed: ${err.message}`
          : "WebGL setup failed.",
      );
      return;
    }

    reglRef.current = regl;

    const positionBuffer = regl.buffer(data.positions);
    const colorBuffer = regl.buffer(data.clusterColors);
    const visibilityBuffer = regl.buffer(visibility ?? data.visibility);
    const expressionBuffer = regl.buffer(
      expressionArr ?? new Float32Array(data.cells.length),
    );
    positionBufferRef.current = positionBuffer;
    colorBufferRef.current = colorBuffer;
    visibilityBufferRef.current = visibilityBuffer;
    expressionBufferRef.current = expressionBuffer;
    cellCountRef.current = data.cells.length;

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
      },
      count: data.cells.length,
      primitive: "points",
      blend: {
        enable: true,
        func: { src: "src alpha", dst: "one minus src alpha" },
      },
      depth: { enable: false },
    });

    drawClustersRef.current = drawClusters;
    drawExpressionRef.current = drawExpression;

    // Size the canvas's backing store to its CSS box × devicePixelRatio
    // so points render crisp on retina/4K. ResizeObserver re-syncs on any
    // container size change (sidebar collapse, window resize, etc.).
    const resizeToParent = () => {
      const parent = canvas.parentElement;
      if (!parent) return;
      const dpr = window.devicePixelRatio || 1;
      const cssW = parent.clientWidth;
      const cssH = height;
      // Bail out if nothing changed — avoids a redraw on every observer tick.
      if (
        canvas.width === Math.floor(cssW * dpr) &&
        canvas.height === Math.floor(cssH * dpr)
      ) {
        return;
      }
      canvas.style.width = `${cssW}px`;
      canvas.style.height = `${cssH}px`;
      canvas.width = Math.floor(cssW * dpr);
      canvas.height = Math.floor(cssH * dpr);
      dirtyRef.current = true;
    };
    resizeToParent();
    const resizeObserver =
      typeof ResizeObserver !== "undefined" ? new ResizeObserver(resizeToParent) : null;
    if (resizeObserver && canvas.parentElement) {
      resizeObserver.observe(canvas.parentElement);
    }

    // Mid-session WebGL context loss — surface the fallback panel.
    const handleContextLost = (e: Event) => {
      e.preventDefault();
      setWebglError(
        "WebGL context was lost. Reload the page to re-render the UMAP.",
      );
    };
    canvas.addEventListener("webglcontextlost", handleContextLost);

    let rafId = 0;
    const tick = () => {
      // Lazy RAF: draw only when something changed. Idle UMAPs cost ~0
      // GPU/CPU between frames; interactions flip dirty and the next
      // frame draws.
      if (dirtyRef.current) {
        regl.poll();
        regl.clear({ color: [0.05, 0.05, 0.08, 1], depth: 1 });
        const expArr = expressionArrRef.current;
        const expRange = expressionRangeRef.current;
        const z = zoomRef.current;
        const t = translateRef.current;
        if (expArr && expRange) {
          drawExpression({
            zoom: z,
            translate: t,
            expMin: expRange.min,
            expMax: expRange.max,
          });
        } else {
          drawClusters({ zoom: z, translate: t });
        }
        dirtyRef.current = false;
      }
      rafId = requestAnimationFrame(tick);
    };
    rafId = requestAnimationFrame(tick);

    return () => {
      cancelAnimationFrame(rafId);
      resizeObserver?.disconnect();
      canvas.removeEventListener("webglcontextlost", handleContextLost);
      positionBuffer.destroy();
      colorBuffer.destroy();
      visibilityBuffer.destroy();
      expressionBuffer.destroy();
      regl.destroy();
      reglRef.current = null;
      positionBufferRef.current = null;
      colorBufferRef.current = null;
      visibilityBufferRef.current = null;
      expressionBufferRef.current = null;
      drawClustersRef.current = null;
      drawExpressionRef.current = null;
      cellCountRef.current = 0;
    };
    // Only re-init on dataset change. Camera and buffer updates flow
    // through their own effects below; including them here would tear
    // down the WebGL context on every pan/zoom frame.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [data]);

  // -------- visibility update (in-place subdata) ------------------------------
  useEffect(() => {
    const buf = visibilityBufferRef.current;
    if (!buf || !visibility) return;
    // Skip the write if the buffer is mid-resize for a new dataset; init
    // will populate it with the latest visibility on the next render.
    if (visibility.length !== cellCountRef.current) return;
    buf.subdata(visibility);
    dirtyRef.current = true;
  }, [visibility]);

  // -------- expression update (in-place subdata) ------------------------------
  useEffect(() => {
    const buf = expressionBufferRef.current;
    if (!buf) return;
    const arr = expressionArr ?? new Float32Array(cellCountRef.current);
    if (arr.length !== cellCountRef.current) return;
    buf.subdata(arr);
    dirtyRef.current = true;
  }, [expressionArr]);

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
      const t = translateRef.current;
      dragState.current = {
        x: e.clientX,
        y: e.clientY,
        origTx: t[0],
        origTy: t[1],
      };
    },
    [],
  );
  const handlePointerMove = useCallback(
    (e: React.PointerEvent<HTMLCanvasElement>) => {
      if (!dragState.current || !canvasRef.current) return;
      const rect = canvasRef.current.getBoundingClientRect();
      const dx = ((e.clientX - dragState.current.x) / rect.width) * 2;
      const dy = -((e.clientY - dragState.current.y) / rect.height) * 2;
      const z = zoomRef.current;
      setTranslate([
        dragState.current.origTx + dx / z,
        dragState.current.origTy + dy / z,
      ]);
    },
    [],
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
  if (webglError) {
    return (
      <div
        role="alert"
        data-testid="expression-umap-fallback"
        style={{
          height,
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          background: "#18181b",
          color: "#fbbf24",
          borderRadius: 8,
          fontFamily: "system-ui, sans-serif",
          padding: 24,
          textAlign: "center",
        }}
      >
        {webglError}
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
    // Canvas dimensions are set imperatively in the init effect against
    // parent.clientWidth × devicePixelRatio. We don't set width/height here.
    <canvas
      ref={canvasRef}
      data-testid="expression-umap-canvas"
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
