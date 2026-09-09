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
/** How far the map can be zoomed, in each direction. */
export const MIN_ZOOM = 0.2;
export const MAX_ZOOM = 50;

/** How near the cursor a point must be, in CSS pixels, to be the one hovered.
 *  Points are drawn at 4px, so this is a little forgiveness around them. */
const HOVER_RADIUS_PX = 8;

/** Which cell is under the cursor, or null when none is near enough.
 *
 * The shader places a point at `(position + translate) * zoom` in clip space,
 * with no aspect correction, so this projects every cell the same way and
 * compares in pixels — which is the space the radius is meaningful in, and the
 * only one where a stretched canvas does not distort the answer.
 *
 * Hidden cells are skipped: a cell filtered off the map should not be
 * identifiable by pointing at where it used to be.
 */
export function pickCell(
  positions: Float32Array,
  visibility: Float32Array | null,
  view: { zoom: number; translate: [number, number]; width: number; height: number },
  cursor: { x: number; y: number },
  radiusPx: number = HOVER_RADIUS_PX,
): number | null {
  const { zoom, translate, width, height } = view;
  if (width === 0 || height === 0) return null;
  let best: number | null = null;
  let bestDistance = radiusPx * radiusPx;
  for (let i = 0; i < positions.length / 2; i++) {
    if (visibility && visibility[i] === 0) continue;
    const clipX = (positions[i * 2] + translate[0]) * zoom;
    const clipY = (positions[i * 2 + 1] + translate[1]) * zoom;
    const px = ((clipX + 1) / 2) * width;
    const py = ((1 - clipY) / 2) * height;
    const dx = px - cursor.x;
    const dy = py - cursor.y;
    const distance = dx * dx + dy * dy;
    if (distance <= bestDistance) {
      bestDistance = distance;
      best = i;
    }
  }
  return best;
}

/** What a hovered cell says about itself.
 *
 * The cell type comes from the catalogue rather than the cell, because the cell
 * carries an ordinal and the ordinal is only meaningful against it. The second
 * line names the genotype and, where the labels were transferred from a
 * reference atlas, which one this cell's came from — a label is worth less
 * without knowing where it came from.
 */
export function describeCell(
  index: number,
  cells: Pick<CellArraysRow, "replicate" | "facets">[],
  clusterOrdinals: Uint8Array,
  clusters: { ordinal: number; cluster_id: string; name: string | null }[],
): { cellType: string; detail: string } | null {
  const cell = cells[index];
  if (!cell) return null;
  const ordinal = clusterOrdinals[index];
  const cluster = clusters.find((c) => c.ordinal === ordinal);
  const cellType =
    ordinal === ORPHAN_CLUSTER_ORDINAL || !cluster
      ? "No cell type"
      : cluster.name || cluster.cluster_id;

  const parts: string[] = [];
  if (cell.replicate) parts.push(cell.replicate);
  const source = cell.facets?.nn_source;
  if (source) parts.push(`label from ${source}`);
  return { cellType, detail: parts.join(" · ") };
}

/** Zoom the map on scroll, and stop the page moving with it.
 *
 * Bound natively rather than through React's `onWheel`, because React attaches
 * its wheel listener to the root passively — and `preventDefault()` inside a
 * passive listener does nothing at all. So the map zoomed and the page scrolled
 * underneath it at the same time.
 *
 * A trackpad pinch arrives here too, as a wheel event with `ctrlKey` set, which
 * the browser would otherwise turn into a zoom of the whole page.
 *
 * Returns the function that removes the listener again.
 */
export function attachWheelZoom(
  canvas: HTMLCanvasElement,
  setZoom: (update: (z: number) => number) => void,
): () => void {
  const onWheel = (e: WheelEvent) => {
    e.preventDefault();
    // A pinch reports far larger deltas than a scroll, so it takes a gentler
    // factor to travel the same distance per gesture.
    const perDelta = e.ctrlKey ? 0.0002 : 0.001;
    const factor = Math.exp(-e.deltaY * perDelta);
    setZoom((z) => Math.min(MAX_ZOOM, Math.max(MIN_ZOOM, z * factor)));
  };
  canvas.addEventListener("wheel", onWheel, { passive: false });
  return () => canvas.removeEventListener("wheel", onWheel);
}

const NORMALIZE_PADDING = 1.05; // give a small border around the UMAP bbox

const ORPHAN_GRAY: [number, number, number] = [0.6, 0.6, 0.6];

export interface ExpressionUmapProps {
  datasetId: number;
  /** Currently-selected gene for expression overlay; null = color by cluster */
  geneName?: string | null;
  /** Clusters currently hidden (ordinal set). Empty = all visible. */
  hiddenClusters?: ReadonlySet<number>;
  /** Values currently hidden, keyed by filter name — the sample column and any
   *  facet the cells carry. Empty = everything visible. */
  hidden?: ReadonlyMap<string, ReadonlySet<string>>;
  /** Height of the canvas in pixels; width fills the parent */
  height?: number;
  /** Fires when data is loaded so parent can render colorbar / sidebar */
  onDataLoaded?: (ctx: {
    dataset: Dataset;
    clusters: Cluster[];
    cellCount: number;
    /** Cells whose `cluster_id` had no row in `scrna_clusters` (sentinel ordinal 255). */
    orphanCount: number;
    /** The filters this dataset offers, in the order they should be shown: the
     *  sample column first, then any facet the cells carry. Empty for a dataset
     *  that records neither. */
    filters: string[];
    /** The cells, so counts can be recomputed as filters change. Counting once
     *  here would leave every number describing the whole dataset. */
    cells: Pick<CellArraysRow, "replicate" | "facets">[];
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

/** Which cells are drawn: 1 for shown, 0 for hidden.
 *
 * Hiding is done here rather than by filtering the cells, and that is the whole
 * reason positions and axis ranges do not move when a sample is switched off:
 * `packPositions` runs once over every cell and never sees the hidden set.
 */
/** The name the sample column goes by among the filters. */
export const SAMPLE_FILTER = "sample";

/** What a cell's value is for one filter, or null when it has none. */
export function filterValue(
  cell: Pick<CellArraysRow, "replicate" | "facets">,
  filter: string,
): string | null {
  if (filter === SAMPLE_FILTER) return cell.replicate;
  return cell.facets?.[filter] ?? null;
}

export function packVisibility(
  cells: Pick<CellArraysRow, "replicate" | "facets">[],
  clusterOrdinals: Uint8Array,
  hiddenClusters: ReadonlySet<number>,
  hidden: ReadonlyMap<string, ReadonlySet<string>> = new Map(),
): Float32Array {
  const out = new Float32Array(cells.length);
  for (let i = 0; i < cells.length; i++) {
    let shown = !hiddenClusters.has(clusterOrdinals[i]);
    if (shown) {
      for (const [filter, values] of hidden) {
        const value = filterValue(cells[i], filter);
        // A cell with no value for a filter is never hidden by it: the filter
        // says nothing about that cell, so it is not something to filter on.
        if (value !== null && values.has(value)) {
          shown = false;
          break;
        }
      }
    }
    out[i] = shown ? 1.0 : 0;
  }
  return out;
}

/** How many cells each value of one filter has, counting only cells the *other*
 *  filters leave visible.
 *
 *  Counting the whole dataset instead would put a number beside a value that
 *  cannot be reached: with only the control genotype showing, the transgene row
 *  would still offer "232 positive" when there are none to see, which reads as
 *  a finding rather than a filter.
 */
export function countsFor(
  cells: Pick<CellArraysRow, "replicate" | "facets">[],
  hidden: ReadonlyMap<string, ReadonlySet<string>>,
  filter: string,
): { name: string; count: number }[] {
  const others = [...hidden].filter(([name]) => name !== filter);
  const counts = new Map<string, number>();
  for (const cell of cells) {
    const value = filterValue(cell, filter);
    if (value === null || value === "") continue;
    const ruledOut = others.some(([name, values]) => {
      const v = filterValue(cell, name);
      return v !== null && values.has(v);
    });
    counts.set(value, (counts.get(value) ?? 0) + (ruledOut ? 0 : 1));
  }
  return [...counts]
    .sort((a, b) => a[0].localeCompare(b[0]))
    .map(([name, count]) => ({ name, count }));
}

export function packPositions(cells: CellArraysRow[]): {
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
  hidden,
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
  /** The cell under the cursor and where to put its label, or null. */
  const [hovered, setHovered] = useState<
    { index: number; x: number; y: number } | null
  >(null);
  const [translate, setTranslate] = useState<[number, number]>([0, 0]);

  const zoomRef = useRef(zoom);
  const translateRef = useRef(translate);
  const expressionArrRef = useRef(expressionArr);
  const expressionRangeRef = useRef(expressionRange);
  const dirtyRef = useRef(true);
  // Hit-testing reads these from a handler that is created once, so they are
  // mirrored here rather than closed over.
  const positionsRef = useRef<Float32Array | null>(null);
  const visibilityRef = useRef<Float32Array | null>(null);
  useEffect(() => {
    zoomRef.current = zoom;
    translateRef.current = translate;
    positionsRef.current = data?.positions ?? null;
    visibilityRef.current = visibility;
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
        // Counted from the cells themselves, so a dataset with different
        // samples — or none — needs no change here.
        let hasSample = false;
        const facetNames = new Set<string>();
        for (let i = 0; i < cells.length; i++) {
          clusterOrdinals[i] = cells[i].cluster_ordinal;
          if (cells[i].cluster_ordinal === ORPHAN_CLUSTER_ORDINAL) orphanCount++;
          const sample = cells[i].replicate;
          if (sample !== null && sample !== "") hasSample = true;
          for (const name of Object.keys(cells[i].facets ?? {})) {
            facetNames.add(name);
          }
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
          filters: [...(hasSample ? [SAMPLE_FILTER] : []), ...facetNames],
          cells,
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
  const visibility = useMemo(
    () =>
      data
        ? packVisibility(
            data.cells,
            data.clusterOrdinals,
            hiddenClusters ?? new Set<number>(),
            hidden ?? new Map(),
          )
        : null,
    [data, hiddenClusters, hidden],
  );

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

  // Wheel is bound natively rather than through onWheel, because React attaches
  // its wheel listener to the root passively -- and preventDefault() inside a
  // passive listener does nothing. The canvas zoomed and the page scrolled
  // underneath it at the same time.
  //
  // A trackpad pinch arrives here too, as a wheel event with ctrlKey set, which
  // the browser would otherwise turn into a zoom of the whole page. Same
  // listener, same preventDefault, so pinching scales the map instead.
  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    return attachWheelZoom(canvas, setZoom);
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
      if (!canvasRef.current) return;
      const rect = canvasRef.current.getBoundingClientRect();

      if (dragState.current) {
        // Dragging: no hover. Identifying cells while the map is moving under
        // the cursor would name a different one every frame.
        const dx = ((e.clientX - dragState.current.x) / rect.width) * 2;
        const dy = -((e.clientY - dragState.current.y) / rect.height) * 2;
        const z = zoomRef.current;
        setTranslate([
          dragState.current.origTx + dx / z,
          dragState.current.origTy + dy / z,
        ]);
        return;
      }

      const x = e.clientX - rect.left;
      const y = e.clientY - rect.top;
      const index = pickCell(
        positionsRef.current ?? new Float32Array(0),
        visibilityRef.current,
        {
          zoom: zoomRef.current,
          translate: translateRef.current,
          width: rect.width,
          height: rect.height,
        },
        { x, y },
      );
      setHovered(index === null ? null : { index, x, y });
    },
    [],
  );

  const handlePointerLeave = useCallback(() => setHovered(null), []);

  const hoveredCell = useMemo(() => {
    if (!hovered || !data) return null;
    const described = describeCell(
      hovered.index, data.cells, data.clusterOrdinals, data.clusters,
    );
    return described && { ...described, x: hovered.x, y: hovered.y };
  }, [hovered, data]);
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
    <div style={{ position: "relative" }}>
      <canvas
        ref={canvasRef}
        data-testid="expression-umap-canvas"
        onPointerDown={handlePointerDown}
        onPointerMove={handlePointerMove}
        onPointerUp={handlePointerUp}
        onPointerLeave={handlePointerLeave}
        style={{
          width: "100%",
          height,
          display: "block",
          borderRadius: 8,
          cursor: dragState.current ? "grabbing" : "grab",
          touchAction: "none",
        }}
      />
      {hoveredCell && (
        <div
          role="status"
          data-testid="expression-umap-tooltip"
          style={{
            position: "absolute",
            // Offset from the cursor so the point stays visible under it, and
            // flipped near the right edge so the label never leaves the canvas.
            left: hoveredCell.x > 0.8 * (canvasRef.current?.clientWidth ?? 0)
              ? undefined
              : hoveredCell.x + 14,
            right: hoveredCell.x > 0.8 * (canvasRef.current?.clientWidth ?? 0)
              ? (canvasRef.current?.clientWidth ?? 0) - hoveredCell.x + 14
              : undefined,
            top: Math.max(0, hoveredCell.y - 12),
            pointerEvents: "none",
            background: "rgba(24,24,27,0.94)",
            color: "#fafaf9",
            borderRadius: 6,
            padding: "6px 9px",
            fontSize: 12,
            lineHeight: 1.45,
            maxWidth: 260,
            whiteSpace: "nowrap",
            overflow: "hidden",
            textOverflow: "ellipsis",
          }}
        >
          <div style={{ fontWeight: 600 }}>{hoveredCell.cellType}</div>
          {hoveredCell.detail && (
            <div style={{ color: "#a8a29e" }}>{hoveredCell.detail}</div>
          )}
        </div>
      )}
    </div>
  );
}

export default ExpressionUmap;
