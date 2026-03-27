"use client";

import { useEffect, useRef } from "react";
import * as d3 from "d3";
import { getSpeciesColor } from "./constants";
import type { KnnResult } from "./GeneSearch";

interface KnnGraphProps {
  results: KnnResult[];
  queryUid: string;
}

interface GraphNode extends d3.SimulationNodeDatum {
  id: string;
  species: string;
  gene_id: string;
  similarity: number;
  rank: number;
  isQuery: boolean;
  orthogroup?: string | null;
  orthogroupShared?: boolean;
}

interface GraphLink extends d3.SimulationLinkDatum<GraphNode> {
  similarity: number;
}

export default function KnnGraph({ results, queryUid }: KnnGraphProps) {
  const svgRef = useRef<SVGSVGElement>(null);
  const containerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!svgRef.current || !containerRef.current || results.length === 0) return;

    const container = containerRef.current;
    const width = container.clientWidth;
    const height = 500;

    const querySpecies = queryUid.split(":")[0];
    const queryGeneId = queryUid.split(":").slice(1).join(":");

    const nodesMap = new Map<string, GraphNode>();

    nodesMap.set(queryUid, {
      id: queryUid,
      species: querySpecies,
      gene_id: queryGeneId,
      similarity: 1,
      rank: 0,
      isQuery: true,
    });

    for (const r of results) {
      if (!nodesMap.has(r.uid)) {
        nodesMap.set(r.uid, {
          id: r.uid,
          species: r.species,
          gene_id: r.gene_id,
          similarity: r.similarity,
          rank: r.rank,
          isQuery: false,
          orthogroup: r.orthogroup,
          orthogroupShared: r.orthogroupShared,
        });
      }
    }

    const nodes = Array.from(nodesMap.values());

    const links: GraphLink[] = results
      .filter((r) => r.uid !== queryUid)
      .map((r) => ({
        source: queryUid,
        target: r.uid,
        similarity: r.similarity,
      }));

    const svg = d3.select(svgRef.current);
    svg.selectAll("*").remove();
    svg.attr("width", width).attr("height", height).attr("viewBox", `0 0 ${width} ${height}`);

    const simMin = d3.min(links, (l) => l.similarity) ?? 0;
    const simMax = d3.max(links, (l) => l.similarity) ?? 1;

    const linkDistance = d3.scaleLinear().domain([simMin, simMax]).range([200, 40]);
    const linkOpacity = d3.scaleLinear().domain([simMin, simMax]).range([0.15, 0.6]);
    const linkWidth = d3.scaleLinear().domain([simMin, simMax]).range([0.5, 2.5]);

    const simulation = d3
      .forceSimulation<GraphNode>(nodes)
      .force(
        "link",
        d3.forceLink<GraphNode, GraphLink>(links).id((d) => d.id).distance((d) => linkDistance(d.similarity))
      )
      .force("charge", d3.forceManyBody().strength(-80))
      .force("center", d3.forceCenter(width / 2, height / 2))
      .force("collision", d3.forceCollide().radius(12));

    const link = svg
      .append("g")
      .selectAll("line")
      .data(links)
      .join("line")
      .attr("stroke", "#94a3b8")
      .attr("stroke-opacity", (d) => linkOpacity(d.similarity))
      .attr("stroke-width", (d) => linkWidth(d.similarity));

    const nodeRadius = (d: GraphNode) => (d.isQuery ? 12 : 10);

    const nodeGroup = svg
      .append("g")
      .selectAll<SVGGElement, GraphNode>("g")
      .data(nodes)
      .join("g")
      .style("cursor", "pointer");

    // Outer ring for orthogroup info
    nodeGroup
      .filter((d) => !!d.orthogroup)
      .append("circle")
      .attr("r", (d) => nodeRadius(d) + 3)
      .attr("fill", "none")
      .attr("stroke", (d) => (d.orthogroupShared ? "#10b981" : "#9ca3af"))
      .attr("stroke-width", 2);

    nodeGroup
      .append("circle")
      .attr("r", nodeRadius)
      .attr("fill", (d) => getSpeciesColor(d.species))
      .attr("stroke", (d) => (d.isQuery ? "#1F2937" : "#fff"))
      .attr("stroke-width", (d) => (d.isQuery ? 2.5 : 1.5));

    nodeGroup
      .append("text")
      .text((d) => (d.isQuery ? "Q" : String(d.rank)))
      .attr("text-anchor", "middle")
      .attr("dominant-baseline", "central")
      .attr("font-size", (d) => (d.isQuery ? "9px" : "8px"))
      .attr("font-weight", "600")
      .attr("fill", "#fff")
      .attr("pointer-events", "none");

    const tooltip = d3
      .select(container)
      .append("div")
      .attr("class", "absolute pointer-events-none bg-white border border-stone-200 rounded-lg px-3 py-2 text-xs shadow-lg hidden")
      .style("z-index", "100");

    nodeGroup
      .on("mouseover", (event, d) => {
        tooltip
          .classed("hidden", false)
          .html(
            `<div class="font-semibold text-neutral-800">${d.gene_id}</div>` +
              `<div class="text-neutral-500 capitalize">${d.species}</div>` +
              (d.isQuery
                ? `<div class="text-blue-600 mt-0.5">Query gene</div>`
                : `<div class="text-neutral-500 mt-0.5">#${d.rank} — Similarity: ${d.similarity.toFixed(4)}</div>`) +
              (d.orthogroup
                ? `<div class="text-emerald-600 mt-0.5">OrthoFinder: ${d.orthogroup}</div>`
                : "")
          );
      })
      .on("mousemove", (event) => {
        const [x, y] = d3.pointer(event, container);
        tooltip.style("left", `${x + 14}px`).style("top", `${y - 10}px`);
      })
      .on("mouseout", () => {
        tooltip.classed("hidden", true);
      });

    const drag = d3
      .drag<SVGGElement, GraphNode>()
      .on("start", (event, d) => {
        if (!event.active) simulation.alphaTarget(0.3).restart();
        d.fx = d.x;
        d.fy = d.y;
      })
      .on("drag", (event, d) => {
        d.fx = event.x;
        d.fy = event.y;
      })
      .on("end", (event, d) => {
        if (!event.active) simulation.alphaTarget(0);
        d.fx = null;
        d.fy = null;
      });

    nodeGroup.call(drag);

    simulation.on("tick", () => {
      link
        .attr("x1", (d) => (d.source as GraphNode).x!)
        .attr("y1", (d) => (d.source as GraphNode).y!)
        .attr("x2", (d) => (d.target as GraphNode).x!)
        .attr("y2", (d) => (d.target as GraphNode).y!);

      nodeGroup.attr("transform", (d) => `translate(${d.x},${d.y})`);
    });

    return () => {
      simulation.stop();
      tooltip.remove();
    };
  }, [results, queryUid]);

  if (results.length === 0) return null;

  return (
    <div>
      <h3 className="text-sm font-semibold text-neutral-700 mb-2">
        KNN Network Graph
      </h3>
      <div
        ref={containerRef}
        className="relative border border-stone-200 rounded-lg bg-white overflow-hidden"
      >
        <svg ref={svgRef} />
        <div className="absolute bottom-3 left-3 flex gap-3 bg-white/90 border border-stone-200 rounded px-3 py-1.5 text-xs">
          {Array.from(new Set(results.map((r) => r.species))).map((species) => (
            <span key={species} className="flex items-center gap-1.5">
              <span
                className="w-2.5 h-2.5 rounded-full"
                style={{ backgroundColor: getSpeciesColor(species) }}
              />
              <span className="capitalize text-neutral-600">{species}</span>
            </span>
          ))}
          {results.some((r) => r.orthogroupShared) && (
            <span className="flex items-center gap-1.5">
              <span className="w-2.5 h-2.5 rounded-full border-2 border-emerald-500" />
              <span className="text-neutral-600">Same orthogroup</span>
            </span>
          )}
          {results.some((r) => r.orthogroup && !r.orthogroupShared) && (
            <span className="flex items-center gap-1.5">
              <span className="w-2.5 h-2.5 rounded-full border-2 border-gray-400" />
              <span className="text-neutral-600">Different orthogroup</span>
            </span>
          )}
        </div>
      </div>
    </div>
  );
}
