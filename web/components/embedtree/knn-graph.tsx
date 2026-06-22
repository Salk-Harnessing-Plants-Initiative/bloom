"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import * as d3 from "d3";
import { speciesColor } from "./constants";
import { cosineDistance } from "./lib/distance";

export type KnnNeighbor = {
  uid: string;
  species: string | null;
  gene_id: string | null;
  similarity: number;
};

type Props = {
  queryUid: string;
  querySpecies: string | null;
  queryGeneId: string | null;
  neighbors: KnnNeighbor[];
  embeddings: Map<string, number[]>;
  showQueryEdges?: boolean;
  showPairwiseEdges?: boolean;
  onSelectNeighbor?: (n: KnnNeighbor) => void;
  width?: number;
  height?: number;
};

type Node = d3.SimulationNodeDatum & {
  id: string;
  isQuery: boolean;
  label: string;
  species: string | null;
  similarity: number;
};

type Link = d3.SimulationLinkDatum<Node> & {
  similarity: number;
  // Edges from query → neighbor are emphasized; pairwise neighbor edges are
  // backdrop-style so the graph reads as "query at the center with its
  // similarity neighborhood, plus how those neighbors relate."
  kind: "query" | "pairwise";
};

const NODE_R_MIN = 5;
const NODE_R_QUERY = 14;
const NODE_R_MAX = 12;

// Edges below this similarity are dropped to keep the graph readable. At
// K=20 that's up to 190 pairwise edges; with ESM-2 data, sim>=0.5 typically
// retains the meaningful relationships and discards the noise.
const PAIRWISE_SIM_THRESHOLD = 0.5;

/**
 * Force-directed graph: query at the center, K neighbors connected by query
 * edges, plus pairwise edges between neighbors above
 * PAIRWISE_SIM_THRESHOLD. The simulation stays live so the user can drag
 * any node to rearrange the layout — drag pins the node where dropped;
 * double-click releases it back to the simulation.
 *
 * Click a neighbor to fire `onSelectNeighbor` (the parent uses this to
 * make that neighbor the new query and re-run KNN).
 */
export function KnnGraph({
  queryUid,
  querySpecies,
  queryGeneId,
  neighbors,
  embeddings,
  showQueryEdges = true,
  showPairwiseEdges = true,
  onSelectNeighbor,
  width = 760,
  height = 520,
}: Props) {
  const svgRef = useRef<SVGSVGElement | null>(null);
  const simRef = useRef<d3.Simulation<Node, Link> | null>(null);
  // dragMovedRef tracks whether the mouse actually moved between mouse-down
  // and mouse-up so we can distinguish a plain click (pivot to that node)
  // from a drag (re-position only). Plain React state would be too slow —
  // the click event reads the captured closure value, not the live state.
  const dragMovedRef = useRef(false);
  const [renderedNodes, setRenderedNodes] = useState<Node[]>([]);
  const [renderedLinks, setRenderedLinks] = useState<Link[]>([]);
  const [hoveredId, setHoveredId] = useState<string | null>(null);
  const [dragId, setDragId] = useState<string | null>(null);

  // Build nodes + links from props. Pairwise edges are computed only if we
  // have embeddings for both endpoints — otherwise we silently degrade to
  // the query-centric star graph.
  const built = useMemo(() => {
    const ns: Node[] = [
      {
        id: queryUid,
        isQuery: true,
        label: queryGeneId ?? queryUid,
        species: querySpecies,
        similarity: 1.0,
      },
      ...neighbors
        .filter((n) => n.uid !== queryUid)
        .map<Node>((n) => ({
          id: n.uid,
          isQuery: false,
          label: n.gene_id ?? n.uid,
          species: n.species,
          similarity: n.similarity,
        })),
    ];

    const ls: Link[] = [];
    // Query → neighbor (always drawn).
    for (const n of ns) {
      if (n.isQuery) continue;
      ls.push({ source: queryUid, target: n.id, similarity: n.similarity, kind: "query" });
    }
    // Neighbor ↔ neighbor (pairwise, above threshold).
    const neighborIds = ns.filter((n) => !n.isQuery).map((n) => n.id);
    for (let i = 0; i < neighborIds.length; i += 1) {
      const ei = embeddings.get(neighborIds[i]);
      if (!ei) continue;
      for (let j = i + 1; j < neighborIds.length; j += 1) {
        const ej = embeddings.get(neighborIds[j]);
        if (!ej) continue;
        const sim = 1 - cosineDistance(ei, ej);
        if (sim < PAIRWISE_SIM_THRESHOLD) continue;
        ls.push({
          source: neighborIds[i],
          target: neighborIds[j],
          similarity: sim,
          kind: "pairwise",
        });
      }
    }

    return { ns, ls };
  }, [queryUid, querySpecies, queryGeneId, neighbors, embeddings]);

  // Build and start the live simulation on input change. Query node is
  // softly pinned to the center via fx/fy; users can still drag it.
  useEffect(() => {
    if (built.ns.length === 0) {
      setRenderedNodes([]);
      setRenderedLinks([]);
      return;
    }

    const query = built.ns.find((n) => n.isQuery);
    if (query) {
      query.fx = width / 2;
      query.fy = height / 2;
    }

    const sim = d3
      .forceSimulation<Node, Link>(built.ns)
      .force(
        "link",
        d3
          .forceLink<Node, Link>(built.ls)
          .id((n) => n.id)
          // Higher similarity ⇒ shorter target distance.
          .distance((l) => 100 + (1 - clamp01(l.similarity)) * 280)
          .strength((l) => (l.kind === "query" ? 0.7 : 0.25)),
      )
      // Stronger repulsion than the default to keep labels readable.
      .force("charge", d3.forceManyBody<Node>().strength(-450))
      .force("center", d3.forceCenter(width / 2, height / 2))
      .force(
        "collide",
        // Wider collision padding so node labels don't overlap.
        d3
          .forceCollide<Node>()
          .radius((n) => (n.isQuery ? NODE_R_QUERY : NODE_R_MAX) + 22)
          .strength(0.9),
      )
      .alpha(1)
      .alphaDecay(0.04)
      .on("tick", () => {
        // Snapshot positions to React state on each tick.
        setRenderedNodes([...sim.nodes()]);
        setRenderedLinks([...(sim.force("link") as d3.ForceLink<Node, Link>).links()]);
      });

    simRef.current = sim;
    return () => {
      sim.stop();
      simRef.current = null;
    };
  }, [built, width, height]);

  // Translate a screen-space pointer to SVG coordinates. Used by drag.
  const screenToSvg = useCallback((clientX: number, clientY: number) => {
    const svg = svgRef.current;
    if (!svg) return { x: 0, y: 0 };
    const pt = svg.createSVGPoint();
    pt.x = clientX;
    pt.y = clientY;
    const transformed = pt.matrixTransform(svg.getScreenCTM()?.inverse());
    return { x: transformed.x, y: transformed.y };
  }, []);

  // Mouse-down on a node starts a drag. The node is pinned (fx/fy) for the
  // duration of the drag; simulation alpha is bumped so the layout
  // responds. Mouse-up ends the drag but leaves the node pinned where
  // released — double-click to unpin.
  const onNodeMouseDown = useCallback(
    (e: React.MouseEvent<SVGGElement>, node: Node) => {
      // Query is locked at center — not draggable.
      if (node.isQuery) return;
      e.stopPropagation();
      e.preventDefault();
      dragMovedRef.current = false;
      setDragId(node.id);
      simRef.current?.alphaTarget(0.3).restart();
      node.fx = node.x ?? 0;
      node.fy = node.y ?? 0;
    },
    [],
  );

  const onSvgMouseMove = useCallback(
    (e: React.MouseEvent<SVGSVGElement>) => {
      if (!dragId) return;
      const node = simRef.current?.nodes().find((n) => n.id === dragId);
      if (!node) return;
      const { x, y } = screenToSvg(e.clientX, e.clientY);
      // Only mark as a real drag if the cursor actually moved noticeably.
      // A few pixels of jitter is normal even on a "click" — distinguish
      // intent by movement, not by whether mouse-down fired.
      const dx = (node.fx ?? x) - x;
      const dy = (node.fy ?? y) - y;
      if (dx * dx + dy * dy > 9) dragMovedRef.current = true;
      node.fx = x;
      node.fy = y;
    },
    [dragId, screenToSvg],
  );

  const onSvgMouseUp = useCallback(() => {
    if (!dragId) return;
    setDragId(null);
    simRef.current?.alphaTarget(0);
  }, [dragId]);

  // Double-click a node to unpin it (the query stays pinned to center).
  const onNodeDoubleClick = useCallback(
    (e: React.MouseEvent<SVGGElement>, node: Node) => {
      e.stopPropagation();
      if (node.isQuery) return;
      node.fx = null;
      node.fy = null;
      simRef.current?.alpha(0.3).restart();
    },
    [],
  );

  if (renderedNodes.length === 0) {
    return (
      <div
        className="flex h-full w-full items-center justify-center rounded-md border border-stone-200 bg-stone-50 text-sm text-neutral-500"
        style={{ width, height }}
      >
        Search for a gene to render its similarity neighborhood.
      </div>
    );
  }

  return (
    <div className="relative">
      <svg
        ref={svgRef}
        width={width}
        height={height}
        onMouseMove={onSvgMouseMove}
        onMouseUp={onSvgMouseUp}
        onMouseLeave={onSvgMouseUp}
        className="rounded-md border border-stone-200 bg-white"
      >
        <defs>
          {/* Soft neon glow for the query-gene ring. */}
          <filter id="query-glow" x="-50%" y="-50%" width="200%" height="200%">
            <feGaussianBlur stdDeviation="3.5" result="blurred" />
            <feMerge>
              <feMergeNode in="blurred" />
              <feMergeNode in="SourceGraphic" />
            </feMerge>
          </filter>
        </defs>
        {/* edges */}
        <g>
          {renderedLinks
            .filter((l) =>
              l.kind === "query" ? showQueryEdges : showPairwiseEdges,
            )
            .map((l, i) => {
              const s = l.source as Node;
              const t = l.target as Node;
              const isQueryEdge = l.kind === "query";
              return (
                <line
                  key={i}
                  x1={s.x}
                  y1={s.y}
                  x2={t.x}
                  y2={t.y}
                  stroke={isQueryEdge ? "#94a3b8" : "#cbd5e1"}
                  strokeOpacity={isQueryEdge ? 0.85 : 0.45 + l.similarity * 0.4}
                  strokeWidth={
                    isQueryEdge ? 1 + l.similarity * 2 : 0.5 + l.similarity * 1.5
                  }
                />
              );
            })}
        </g>
        {/* nodes */}
        <g>
          {renderedNodes.map((n) => {
            const r = n.isQuery
              ? NODE_R_QUERY
              : NODE_R_MIN + (NODE_R_MAX - NODE_R_MIN) * clamp01(n.similarity);
            const hovered = hoveredId === n.id;
            const dragging = dragId === n.id;
            const pinned = n.fx != null && n.fy != null && !n.isQuery;
            return (
              <g
                key={n.id}
                transform={`translate(${n.x ?? 0},${n.y ?? 0})`}
                onMouseEnter={() => setHoveredId(n.id)}
                onMouseLeave={() =>
                  setHoveredId((id) => (id === n.id ? null : id))
                }
                onMouseDown={(e) => onNodeMouseDown(e, n)}
                onDoubleClick={(e) => onNodeDoubleClick(e, n)}
                onClick={(e) => {
                  e.stopPropagation();
                  if (n.isQuery) return;
                  // Suppress the click if the user actually dragged — drag
                  // is a layout edit, click is a pivot.
                  if (dragMovedRef.current) {
                    dragMovedRef.current = false;
                    return;
                  }
                  onSelectNeighbor?.({
                    uid: n.id,
                    species: n.species,
                    gene_id: n.label,
                    similarity: n.similarity,
                  });
                }}
                className={n.isQuery ? "cursor-grab" : "cursor-pointer"}
                style={dragging ? { cursor: "grabbing" } : undefined}
              >
                {n.isQuery && (
                  <>
                    {/* Outer neon glow ring. */}
                    <circle
                      r={r + 8}
                      fill="none"
                      stroke="#fbbf24"
                      strokeWidth={3}
                      strokeOpacity={0.9}
                      filter="url(#query-glow)"
                    />
                    {/* Inner crisp ring on top of the glow for definition. */}
                    <circle
                      r={r + 4}
                      fill="none"
                      stroke="#f59e0b"
                      strokeWidth={1.5}
                    />
                  </>
                )}
                <circle
                  r={r}
                  fill={speciesColor(n.species)}
                  stroke={n.isQuery ? "#92400e" : hovered ? "#111827" : "#ffffff"}
                  strokeWidth={n.isQuery ? 2 : hovered ? 1.5 : 1}
                />
                {pinned && (
                  <circle
                    r={r + 4}
                    fill="none"
                    stroke="#111827"
                    strokeWidth={0.8}
                    strokeDasharray="2 2"
                  />
                )}
                <text
                  y={r + (n.isQuery ? 18 : 12)}
                  textAnchor="middle"
                  className="select-none fill-neutral-700"
                  style={{ fontSize: 11, fontFamily: "ui-monospace, monospace" }}
                >
                  {truncate(n.label, 14)}
                </text>
                {n.isQuery && (
                  <text
                    y={r + 32}
                    textAnchor="middle"
                    className="select-none"
                    style={{
                      fontSize: 9,
                      fontWeight: 600,
                      letterSpacing: "0.06em",
                      textTransform: "uppercase",
                      fill: "#b45309",
                    }}
                  >
                    Query Gene
                  </text>
                )}
              </g>
            );
          })}
        </g>
      </svg>
      {hoveredId && (
        <Tooltip
          nodes={renderedNodes}
          hoveredId={hoveredId}
          width={width}
        />
      )}
      <div className="absolute bottom-2 right-2 rounded bg-white/90 px-2 py-1 text-[10px] text-neutral-500 shadow">
        drag to move · double-click to unpin · click neighbor to pivot
      </div>
    </div>
  );
}

function Tooltip({
  nodes,
  hoveredId,
  width,
}: {
  nodes: Node[];
  hoveredId: string;
  width: number;
}) {
  const n = nodes.find((x) => x.id === hoveredId);
  if (!n) return null;
  return (
    <div
      className="pointer-events-none absolute right-2 top-2 rounded-md bg-neutral-900/90 px-3 py-2 text-xs text-white shadow-lg"
      style={{ maxWidth: width / 2 }}
    >
      <div className="font-mono">{n.label}</div>
      <div className="text-neutral-300">
        species: {n.species ?? "—"}
        {!n.isQuery && (
          <>
            {" · "}sim: {n.similarity.toFixed(3)}
          </>
        )}
        {n.isQuery && <> · query</>}
      </div>
    </div>
  );
}

function clamp01(x: number): number {
  if (Number.isNaN(x)) return 0;
  if (x < 0) return 0;
  if (x > 1) return 1;
  return x;
}

function truncate(s: string, n: number): string {
  if (s.length <= n) return s;
  return s.slice(0, n - 1) + "…";
}
