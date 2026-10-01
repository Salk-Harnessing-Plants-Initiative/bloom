"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import createREGL, { type Regl, type Buffer as ReglBuffer } from "regl";

import { attachWheelZoom, isClick } from "@/components/expression-umap";
import { CLUSTER_FRAG, POINT_VERT } from "@/components/expression-lib/shaders";
import { POINT_BLEND } from "@/components/expression-lib/point-blend";
import {
  fillHeightFor,
  fitFor,
  mapHeightFor,
  MIN_MAP_HEIGHT,
  panBy,
  pickPoint,
  projectPoint,
} from "@/components/integration-lib/joint-view";
import { UmapZoomBar } from "@/components/umap-zoom-bar";
import { UmapLabelLayer, type MapLabel } from "@/components/umap-label-layer";
import { projectXY } from "@/components/expression-lib/umap-labels";

/** Point size in device pixels at zoom 1, as on the dataset map. */
const POINT_SIZE = 4;
/** Points grow as the map is zoomed in, up to this many times their size, so a
 *  crowded region opens up into points big enough to click. */
const MAX_POINT_GROWTH = 3;
const CLEAR_COLOUR: [number, number, number, number] = [0.05, 0.05, 0.08, 1];
const WEBGL_REQUIRED =
  "WebGL is required for this map. Please enable WebGL or update your browser.";

const NO_LABELS: MapLabel[] = [];

export function pointSizeAt(zoom: number): number {
  return POINT_SIZE * Math.min(MAX_POINT_GROWTH, Math.max(1, Math.sqrt(zoom)));
}

export interface IntegrationUmapProps {
  /** Two floats per point, already centred and scaled. */
  positions: Float32Array;
  /** Four floats per point. */
  colours: Float32Array;
  /** 1 for a point drawn, 0 for one hidden. */
  visibility: Float32Array;
  /** 1 for a point in focus, 0 for one greyed out. */
  focus: Float32Array;
  focusSet: boolean;
  /** The point whose details are open, ringed on the map. */
  selected: number | null;
  describe: (index: number) => { title: string; detail: string };
  /** A click on a point, or null for a click on empty space. */
  onPick: (index: number | null) => void;
  /** Run down to the bottom of the window, for full screen, rather than
   *  staying square to the width. */
  fill?: boolean;
  /** Names written over the map, each on its cluster. */
  labels?: MapLabel[];
}

/** The joint map's canvas, as wide as its column and as tall as the window
 *  allows: draws the points it is given, pans, zooms, and says which point is
 *  under the cursor. What to draw is worked out by its parent. */
export function IntegrationUmap({
  positions,
  colours,
  visibility,
  focus,
  focusSet,
  selected,
  describe,
  onPick,
  fill = false,
  labels = NO_LABELS,
}: IntegrationUmapProps) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const buffersRef = useRef<
    { colour: ReglBuffer; visible: ReglBuffer; focus: ReglBuffer; n: number } | null
  >(null);
  const [webglError, setWebglError] = useState<string | null>(null);
  const [zoom, setZoom] = useState(1);
  const [translate, setTranslate] = useState<[number, number]>([0, 0]);
  const [size, setSize] = useState({ width: 0, height: MIN_MAP_HEIGHT });
  const [hovered, setHovered] = useState<{ index: number; x: number; y: number } | null>(null);

  // The render loop and pointer handlers are made once, so they read these.
  const live = useRef({
    zoom, translate, size, fill, focusSet, positions, visibility, onPick, dirty: true,
  });
  useEffect(() => {
    live.current = {
      zoom, translate, size, fill, focusSet, positions, visibility, onPick, dirty: true,
    };
  });

  // Going in or out of full screen resizes the canvas at once.
  const resizeRef = useRef<(() => void) | null>(null);
  useEffect(() => {
    resizeRef.current?.();
  }, [fill]);

  // -------- regl init + render loop, once per set of positions --------------
  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    setWebglError(null);
    let regl: Regl | undefined;
    try {
      regl = createREGL({
        canvas,
        attributes: { antialias: true, preserveDrawingBuffer: false },
        onDone: (err) => {
          if (err) setWebglError(WEBGL_REQUIRED);
        },
      });
    } catch {
      setWebglError(WEBGL_REQUIRED);
      return;
    }
    if (!regl) return;
    const gl = regl;

    const n = positions.length / 2;
    const position = gl.buffer(positions);
    const colour = gl.buffer(colours);
    const visible = gl.buffer(visibility);
    const focusBuffer = gl.buffer(focus);
    buffersRef.current = { colour, visible, focus: focusBuffer, n };

    const draw = gl({
      vert: POINT_VERT,
      frag: CLUSTER_FRAG,
      attributes: { position, color: colour, visible, focus: focusBuffer },
      uniforms: {
        zoom: gl.prop<{ zoom: number }, "zoom">("zoom"),
        translate: gl.prop<{ translate: [number, number] }, "translate">("translate"),
        fit: gl.prop<{ fit: [number, number] }, "fit">("fit"),
        pointSize: gl.prop<{ pointSize: number }, "pointSize">("pointSize"),
        focusMode: gl.prop<{ focusMode: number }, "focusMode">("focusMode"),
      },
      count: n,
      primitive: "points",
      blend: POINT_BLEND,
      depth: { enable: false },
    });

    // The canvas fills its column and is as tall as the window allows; the
    // backing store follows its box × devicePixelRatio, so points stay crisp.
    const resize = () => {
      const parent = canvas.parentElement;
      if (!parent) return;
      const dpr = window.devicePixelRatio || 1;
      const width = parent.clientWidth;
      const height = live.current.fill
        ? fillHeightFor(parent.getBoundingClientRect().top, window.innerHeight)
        : mapHeightFor(width, window.innerHeight);
      live.current.size = { width, height };
      setSize((prev) =>
        prev.width === width && prev.height === height ? prev : { width, height },
      );
      if (canvas.width === Math.floor(width * dpr) && canvas.height === Math.floor(height * dpr)) {
        return;
      }
      canvas.width = Math.floor(width * dpr);
      canvas.height = Math.floor(height * dpr);
      live.current.dirty = true;
    };
    resize();
    resizeRef.current = resize;
    const observer = typeof ResizeObserver !== "undefined" ? new ResizeObserver(resize) : null;
    if (observer && canvas.parentElement) observer.observe(canvas.parentElement);
    window.addEventListener("resize", resize);

    const onContextLost = (e: Event) => {
      e.preventDefault();
      setWebglError("WebGL context was lost. Reload the page to draw the map again.");
    };
    canvas.addEventListener("webglcontextlost", onContextLost);

    let raf = 0;
    const tick = () => {
      const v = live.current;
      // Draws only when something changed, so an idle map costs nothing.
      if (v.dirty) {
        gl.poll();
        gl.clear({ color: CLEAR_COLOUR, depth: 1 });
        const pointSize = pointSizeAt(v.zoom);
        const fit = fitFor(v.size.width, v.size.height);
        // With a focus set, the points outside it go down first in grey and the
        // points inside it are drawn over them in colour.
        for (const focusMode of v.focusSet ? [1, 2] : [0]) {
          draw({ zoom: v.zoom, translate: v.translate, fit, pointSize, focusMode });
        }
        v.dirty = false;
      }
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);

    return () => {
      cancelAnimationFrame(raf);
      observer?.disconnect();
      window.removeEventListener("resize", resize);
      canvas.removeEventListener("webglcontextlost", onContextLost);
      position.destroy();
      colour.destroy();
      visible.destroy();
      focusBuffer.destroy();
      gl.destroy();
      buffersRef.current = null;
      resizeRef.current = null;
    };
    // Only new positions rebuild the context; colours, visibility and focus
    // are written into the existing buffers below.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [positions]);

  useEffect(() => {
    const b = buffersRef.current;
    if (!b || colours.length !== b.n * 4) return;
    b.colour.subdata(colours);
    live.current.dirty = true;
  }, [colours]);

  useEffect(() => {
    const b = buffersRef.current;
    if (!b || visibility.length !== b.n) return;
    b.visible.subdata(visibility);
    live.current.dirty = true;
  }, [visibility]);

  useEffect(() => {
    const b = buffersRef.current;
    if (!b || focus.length !== b.n) return;
    b.focus.subdata(focus);
    live.current.dirty = true;
  }, [focus]);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    return attachWheelZoom(canvas, setZoom);
  }, []);

  // -------- pointer: drag pans, hover names, click picks ---------------------
  const drag = useRef<{ x: number; y: number; tx: number; ty: number } | null>(null);

  const viewNow = () => {
    const v = live.current;
    return { zoom: v.zoom, translate: v.translate, width: v.size.width, height: v.size.height };
  };

  const pointAt = (clientX: number, clientY: number) => {
    const canvas = canvasRef.current;
    if (!canvas) return null;
    const rect = canvas.getBoundingClientRect();
    const x = clientX - rect.left;
    const y = clientY - rect.top;
    const v = live.current;
    const index = pickPoint(v.positions, v.visibility, viewNow(), { x, y });
    return index === null ? null : { index, x, y };
  };

  const onPointerDown = useCallback((e: React.PointerEvent<HTMLCanvasElement>) => {
    e.currentTarget.setPointerCapture(e.pointerId);
    const [tx, ty] = live.current.translate;
    drag.current = { x: e.clientX, y: e.clientY, tx, ty };
    setHovered(null);
  }, []);

  const onPointerMove = useCallback((e: React.PointerEvent<HTMLCanvasElement>) => {
    const down = drag.current;
    if (down) {
      const [dx, dy] = panBy(e.clientX - down.x, e.clientY - down.y, viewNow());
      setTranslate([down.tx + dx, down.ty + dy]);
      return;
    }
    setHovered(pointAt(e.clientX, e.clientY));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const onPointerUp = useCallback((e: React.PointerEvent<HTMLCanvasElement>) => {
    e.currentTarget.releasePointerCapture(e.pointerId);
    const down = drag.current;
    drag.current = null;
    if (!down || !isClick(down, { x: e.clientX, y: e.clientY })) return;
    live.current.onPick(pointAt(e.clientX, e.clientY)?.index ?? null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const onPointerLeave = useCallback(() => setHovered(null), []);

  const resetView = useCallback(() => {
    setZoom(1);
    setTranslate([0, 0]);
  }, []);

  if (webglError) {
    return (
      <div
        role="alert"
        className="flex items-center justify-center rounded-lg bg-zinc-900 p-6 text-center text-sm text-amber-400"
        style={{ height: size.height }}
      >
        {webglError}
      </div>
    );
  }

  const { width, height } = size;
  const marker =
    selected !== null && width > 0 && selected < positions.length / 2
      ? projectPoint(positions, selected, { zoom, translate, width, height })
      : null;
  const markerShown =
    marker !== null && marker.x >= 0 && marker.x <= width && marker.y >= 0 && marker.y <= height;
  const tip = hovered ? describe(hovered.index) : null;
  const flip = hovered !== null && hovered.x > 0.8 * width;

  return (
    <div className="relative">
      <canvas
        ref={canvasRef}
        data-testid="integration-umap-canvas"
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={onPointerUp}
        onPointerLeave={onPointerLeave}
        style={{
          width: "100%",
          height,
          display: "block",
          borderRadius: 8,
          cursor: "grab",
          touchAction: "none",
        }}
      />
      {markerShown && marker && (
        <div
          aria-hidden
          className="pointer-events-none absolute h-4 w-4 rounded-full border-2 border-white shadow-[0_0_0_2px_rgba(0,0,0,0.55)]"
          style={{ left: marker.x - 8, top: marker.y - 8 }}
        />
      )}
      {tip && hovered && (
        <div
          role="status"
          className="pointer-events-none absolute max-w-[260px] overflow-hidden whitespace-nowrap rounded-md bg-zinc-900/95 px-2.5 py-1.5 text-xs leading-snug text-stone-50"
          style={{
            left: flip ? undefined : hovered.x + 14,
            right: flip ? width - hovered.x + 14 : undefined,
            top: Math.max(0, hovered.y - 12),
          }}
        >
          <div className="truncate font-semibold">{tip.title}</div>
          {tip.detail && <div className="truncate text-stone-400">{tip.detail}</div>}
        </div>
      )}
      <UmapLabelLayer
        labels={labels}
        project={(x, y) => projectXY(x, y, { zoom, translate, width, height }, fitFor(width, height))}
        width={width}
        height={height}
      />
      <UmapZoomBar zoom={zoom} onZoomChange={setZoom} onReset={resetView} />
    </div>
  );
}

export default IntegrationUmap;
