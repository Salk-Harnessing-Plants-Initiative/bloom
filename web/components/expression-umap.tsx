"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import createREGL, { type Regl, type Buffer as ReglBuffer, type DrawCommand } from "regl";

import {
  fetchCells,
  fetchClusters,
  fetchDataset,
  fetchGeneCounts,
  NoStoredExpressionError,
  ORPHAN_CLUSTER_ORDINAL,
  type CellArraysRow,
} from "@/components/expression-lib/scrna-client";
import {
  packCellArrays,
  packClusterColors,
  packPositions,
  focusIsSet,
  packFocus,
  packVisibility,
  type FocusedValues,
  type HiddenValues,
} from "@/components/expression-lib/umap-packing";
import {
  CLUSTER_FRAG,
  EXPRESSION_FRAG,
  EXPRESSION_VERT,
  POINT_VERT,
} from "@/components/expression-lib/shaders";
import { MAX_ZOOM, MIN_ZOOM } from "@/components/expression-lib/umap-zoom";
import { POINT_BLEND } from "@/components/expression-lib/point-blend";
import { UmapZoomBar } from "@/components/umap-zoom-bar";
import { UmapLabelLayer } from "@/components/umap-label-layer";
import { labelAnchors, projectXY } from "@/components/expression-lib/umap-labels";
import { transgeneBadge, transgeneByCluster } from "@/components/expression-lib/transgene";
import type { Database } from "@/lib/database.types";

type Dataset = Database["public"]["Tables"]["scrna_datasets"]["Row"];
type Cluster = Database["public"]["Tables"]["scrna_clusters"]["Row"];

const DEFAULT_POINT_SIZE = 4.0;

/** This map is drawn stretched to its canvas, as it always has been. */
const NO_FIT: [number, number] = [1, 1];

const NO_HIDDEN_VALUES: HiddenValues = new Map();

export { MAX_ZOOM, MIN_ZOOM };

/** A press and release this close together, in CSS pixels, is a click rather
 *  than the start of a pan. */
export const CLICK_SLOP_PX = 4;

export function isClick(down: { x: number; y: number }, up: { x: number; y: number }): boolean {
  return Math.hypot(up.x - down.x, up.y - down.y) <= CLICK_SLOP_PX;
}

/** How near the cursor a point must be, in CSS pixels, to be the one hovered.
 *  Points are drawn at 4px, so this is a little forgiveness around them. */
const HOVER_RADIUS_PX = 8;

/** Which cell is under the cursor, or null when none is near enough.
 *
 * The shader places a point at `(position + translate) * zoom` in clip space,
 * so this projects every cell the same way and compares in pixels. Hidden cells
 * are skipped: a cell filtered off the map should not be identifiable by
 * pointing at where it used to be.
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

/** What a hovered cell says about itself: its cell type from the catalogue, then
 *  its genotype and which reference its label was transferred from. */
export function describeCell(
  index: number,
  cells: Pick<CellArraysRow, "replicate" | "facets" | "genotype">[],
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
  const genotype = cell.genotype ?? cell.replicate;
  if (genotype) parts.push(genotype);
  const source = cell.facets?.nn_source;
  if (source) parts.push(`label from ${source}`);
  return { cellType, detail: parts.join(" · ") };
}

/** Zoom the map on scroll, and stop the page moving with it.
 *
 * Bound natively rather than through React's `onWheel`, because React attaches
 * its wheel listener to the root passively, and `preventDefault()` inside a
 * passive listener does nothing. A trackpad pinch arrives as a wheel event with
 * `ctrlKey` set, which the browser would otherwise turn into a page zoom.
 *
 * Returns the function that removes the listener again.
 */
export function attachWheelZoom(
  canvas: HTMLCanvasElement,
  setZoom: (update: (z: number) => number) => void,
): () => void {
  const onWheel = (e: WheelEvent) => {
    e.preventDefault();
    // A pinch reports far larger deltas than a scroll, so it takes a gentler factor.
    const perDelta = e.ctrlKey ? 0.0002 : 0.001;
    const factor = Math.exp(-e.deltaY * perDelta);
    setZoom((z) => Math.min(MAX_ZOOM, Math.max(MIN_ZOOM, z * factor)));
  };
  canvas.addEventListener("wheel", onWheel, { passive: false });
  return () => canvas.removeEventListener("wheel", onWheel);
}

export interface ExpressionUmapProps {
  datasetId: number;
  /** Currently-selected gene for expression overlay; null = color by cluster */
  geneName?: string | null;
  /** Clusters currently hidden (ordinal set). Empty = all visible. */
  hiddenClusters?: ReadonlySet<number>;
  /** Values hidden per filter row: the sample row and each label. Empty = all visible. */
  hiddenValues?: HiddenValues;
  /** Values focused on per filter row; cells outside the focus are greyed out. */
  focusedValues?: FocusedValues;
  /** Height of the canvas in pixels; width fills the parent */
  height?: number;
  /** Fires when data is loaded so parent can render colorbar / sidebar */
  onDataLoaded?: (ctx: {
    dataset: Dataset;
    clusters: Cluster[];
    cellCount: number;
    /** Cells whose `cluster_id` had no row in `scrna_clusters` (sentinel ordinal 255). */
    orphanCount: number;
    /** The filter rows the cells offer, the sample row first. */
    filters: string[];
    /** Per filter row, the cells with no value for it. */
    unlabelled: Record<string, number>;
    /** The cells, so each row can count what the other rows leave showing. */
    cells: Pick<CellArraysRow, "replicate" | "facets" | "cluster_ordinal">[];
  }) => void;
  /** Fires whenever the currently-overlaid gene's min/max changes */
  onExpressionRangeChanged?: (range: { min: number; max: number } | null) => void;
  /** The part of the gene's range the colours span, from the colour bar; the
   *  gene's whole range when absent. */
  colourRange?: { min: number; max: number } | null;
  /** Why the gene picked cannot be shown, in words, or null once it can. */
  onGeneError?: (message: string | null) => void;
  /** Fires with a cell's cluster ordinal when that cell is clicked */
  onCellClick?: (ordinal: number) => void;
  /** Whether each cluster's label carries its transgene-positive count. */
  showTransgene?: boolean;
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

export function ExpressionUmap({
  datasetId,
  geneName,
  hiddenClusters,
  hiddenValues,
  focusedValues,
  height = 600,
  onDataLoaded,
  onExpressionRangeChanged,
  colourRange,
  onGeneError,
  onCellClick,
  showTransgene = true,
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
  const focusBufferRef = useRef<ReglBuffer | null>(null);
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
  /** The canvas's width in CSS pixels, so the labels can be placed on it. */
  const [canvasWidth, setCanvasWidth] = useState(0);

  const zoomRef = useRef(zoom);
  const translateRef = useRef(translate);
  const expressionArrRef = useRef(expressionArr);
  const expressionRangeRef = useRef(expressionRange);
  const colourRangeRef = useRef(colourRange ?? null);
  const dirtyRef = useRef(true);
  /** Whether a focus is set, so the greyed pass is drawn only then. */
  const focusSetRef = useRef(false);
  // Hit-testing reads these from a handler created once, so they are mirrored here.
  const positionsRef = useRef<Float32Array | null>(null);
  const visibilityRef = useRef<Float32Array | null>(null);
  const ordinalsRef = useRef<Uint8Array | null>(null);
  const onCellClickRef = useRef(onCellClick);
  useEffect(() => {
    zoomRef.current = zoom;
    translateRef.current = translate;
    positionsRef.current = data?.positions ?? null;
    visibilityRef.current = visibility;
    ordinalsRef.current = data?.clusterOrdinals ?? null;
    onCellClickRef.current = onCellClick;
    expressionArrRef.current = expressionArr;
    expressionRangeRef.current = expressionRange;
    colourRangeRef.current = colourRange ?? null;
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
        // Counted from the cells themselves, so a dataset with different
        // samples — or none — needs no change here.
        const { clusterOrdinals, orphanCount, filters, unlabelled } = packCellArrays(cells);
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
          filters,
          unlabelled,
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
      onGeneError?.(null);
      return;
    }
    let cancelled = false;
    (async () => {
      try {
        const arr = await fetchGeneCounts(
          data.dataset.id, geneName, data.cells.length,
        );
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
        onGeneError?.(null);
      } catch (err) {
        if (!cancelled) {
          setExpressionArr(null);
          setExpressionRange(null);
          onExpressionRangeChanged?.(null);
          // The map stays on cell types, and the page says why.
          onGeneError?.(
            err instanceof NoStoredExpressionError
              ? `${geneName} has no stored expression in this dataset.`
              : `Could not load ${geneName}: ${err instanceof Error ? err.message : String(err)}`,
          );
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [data, geneName, onExpressionRangeChanged, onGeneError]);

  // -------- visibility recompute from hidden set -----------------------------
  const visibility = useMemo(
    () =>
      data
        ? packVisibility(
            data.cells,
            data.clusterOrdinals,
            hiddenClusters ?? new Set<number>(),
            hiddenValues ?? NO_HIDDEN_VALUES,
          )
        : null,
    [data, hiddenClusters, hiddenValues],
  );

  // -------- focus recompute from focused values -----------------------------
  const focus = useMemo(
    () => (data ? packFocus(data.cells, focusedValues ?? NO_HIDDEN_VALUES) : null),
    [data, focusedValues],
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
    const focusBuffer = regl.buffer(focus ?? new Float32Array(data.cells.length).fill(1));
    positionBufferRef.current = positionBuffer;
    colorBufferRef.current = colorBuffer;
    visibilityBufferRef.current = visibilityBuffer;
    expressionBufferRef.current = expressionBuffer;
    focusBufferRef.current = focusBuffer;
    cellCountRef.current = data.cells.length;

    const drawClusters = regl({
      vert: POINT_VERT,
      frag: CLUSTER_FRAG,
      attributes: {
        position: positionBuffer,
        color: colorBuffer,
        visible: visibilityBuffer,
        focus: focusBuffer,
      },
      uniforms: {
        zoom: regl.prop<{ zoom: number }, "zoom">("zoom"),
        translate: regl.prop<{ translate: [number, number] }, "translate">("translate"),
        fit: NO_FIT,
        pointSize: DEFAULT_POINT_SIZE,
        focusMode: regl.prop<{ focusMode: number }, "focusMode">("focusMode"),
      },
      count: data.cells.length,
      primitive: "points",
      blend: POINT_BLEND,
      depth: { enable: false },
    });

    const drawExpression = regl({
      vert: EXPRESSION_VERT,
      frag: EXPRESSION_FRAG,
      attributes: {
        position: positionBuffer,
        expression: expressionBuffer,
        visible: visibilityBuffer,
        focus: focusBuffer,
      },
      uniforms: {
        zoom: regl.prop<{ zoom: number }, "zoom">("zoom"),
        translate: regl.prop<{ translate: [number, number] }, "translate">("translate"),
        pointSize: DEFAULT_POINT_SIZE,
        expMin: regl.prop<{ expMin: number }, "expMin">("expMin"),
        expMax: regl.prop<{ expMax: number }, "expMax">("expMax"),
        focusMode: regl.prop<{ focusMode: number }, "focusMode">("focusMode"),
      },
      count: data.cells.length,
      primitive: "points",
      blend: POINT_BLEND,
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
      setCanvasWidth(cssW);
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
        const expRange = colourRangeRef.current ?? expressionRangeRef.current;
        const z = zoomRef.current;
        const t = translateRef.current;
        // With a focus set, the cells outside it go down first in grey and the
        // cells inside it are drawn over them in colour.
        for (const focusMode of focusSetRef.current ? [1, 2] : [0]) {
          if (expArr && expRange) {
            drawExpression({
              zoom: z,
              translate: t,
              expMin: expRange.min,
              expMax: expRange.max,
              focusMode,
            });
          } else {
            drawClusters({ zoom: z, translate: t, focusMode });
          }
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
      focusBuffer.destroy();
      regl.destroy();
      reglRef.current = null;
      positionBufferRef.current = null;
      colorBufferRef.current = null;
      visibilityBufferRef.current = null;
      expressionBufferRef.current = null;
      focusBufferRef.current = null;
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

  // -------- focus update (in-place subdata) -----------------------------------
  useEffect(() => {
    const buf = focusBufferRef.current;
    if (!buf || !focus) return;
    if (focus.length !== cellCountRef.current) return;
    buf.subdata(focus);
    focusSetRef.current = focusIsSet(focusedValues ?? NO_HIDDEN_VALUES);
    dirtyRef.current = true;
  }, [focus, focusedValues]);

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
  // The canvas only exists once the data has loaded, so the listener is bound
  // then, and again for each dataset's canvas.
  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    return attachWheelZoom(canvas, setZoom);
  }, [data]);

  const dragState = useRef<{ x: number; y: number; origTx: number; origTy: number } | null>(
    null,
  );
  /** The visible cell at a point on the canvas, or null. */
  const cellAt = (rect: DOMRect, x: number, y: number): number | null =>
    pickCell(
      positionsRef.current ?? new Float32Array(0),
      visibilityRef.current,
      { zoom: zoomRef.current, translate: translateRef.current,
        width: rect.width, height: rect.height },
      { x, y },
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
        // No hover while dragging: it would name a different cell every frame.
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
      const index = cellAt(rect, x, y);
      setHovered(index === null ? null : { index, x, y });
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [],
  );

  const handlePointerLeave = useCallback(() => setHovered(null), []);

  const resetView = useCallback(() => {
    setZoom(1);
    setTranslate([0, 0]);
  }, []);

  // Each cluster's name, written on its densest patch of shown cells, with a
  // green badge for its transgene-positive cells.
  const transgene = useMemo(() => (data ? transgeneByCluster(data.cells) : null), [data]);
  const mapLabels = useMemo(() => {
    if (!data || !visibility) return [];
    const names = new Map(data.clusters.map((c) => [c.ordinal, c.name || c.cluster_id]));
    const nLevels = data.clusters.reduce((most, c) => Math.max(most, c.ordinal + 1), 0);
    return labelAnchors(data.positions, data.clusterOrdinals, nLevels, visibility)
      .filter((anchor) => names.has(anchor.level))
      .map((anchor) => ({
        text: names.get(anchor.level) ?? "",
        x: anchor.x,
        y: anchor.y,
        badge: showTransgene
          ? transgeneBadge(transgene?.get(anchor.level)?.positive ?? 0) ?? undefined
          : undefined,
      }));
  }, [data, visibility, transgene, showTransgene]);

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
      const down = dragState.current;
      dragState.current = null;
      // A click on a cell picks its cell type; a drag only pans.
      if (!down || !canvasRef.current || !isClick(down, { x: e.clientX, y: e.clientY })) return;
      const rect = canvasRef.current.getBoundingClientRect();
      const index = cellAt(rect, e.clientX - rect.left, e.clientY - rect.top);
      const ordinals = ordinalsRef.current;
      if (index !== null && ordinals) onCellClickRef.current?.(ordinals[index]);
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
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
            // Offset from the cursor so the point stays visible, and flipped near
            // the right edge so the label never leaves the canvas.
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
      <UmapLabelLayer
        labels={mapLabels}
        project={(x, y) => projectXY(x, y, { zoom, translate, width: canvasWidth, height }, NO_FIT)}
        width={canvasWidth}
        height={height}
      />
      <UmapZoomBar zoom={zoom} onZoomChange={setZoom} onReset={resetView} />
    </div>
  );
}

export default ExpressionUmap;
