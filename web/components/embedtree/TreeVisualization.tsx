"use client";

import { useEffect, useRef } from "react";
import * as d3 from "d3";
import { getSpeciesColor } from "./constants";
import { type TreeNode, getLeaves, getSpeciesFromLeaf } from "./lib/newick";

interface TreeVisualizationProps {
  tree: TreeNode;
}

interface HierarchyNode {
  name: string;
  branchLength: number;
  children?: HierarchyNode[];
}

function toHierarchy(node: TreeNode): HierarchyNode {
  return {
    name: node.name,
    branchLength: node.branchLength,
    children: node.children.length > 0 ? node.children.map(toHierarchy) : undefined,
  };
}

export default function TreeVisualization({ tree }: TreeVisualizationProps) {
  const svgRef = useRef<SVGSVGElement>(null);
  const containerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!svgRef.current || !containerRef.current) return;

    const leaves = getLeaves(tree);
    const numLeaves = leaves.length;
    const margin = { top: 20, right: 200, bottom: 20, left: 20 };
    const width = containerRef.current.clientWidth;
    const height = Math.max(300, numLeaves * 22 + margin.top + margin.bottom);
    const innerWidth = width - margin.left - margin.right;
    const innerHeight = height - margin.top - margin.bottom;

    const svg = d3.select(svgRef.current);
    svg.selectAll("*").remove();
    svg.attr("width", width).attr("height", height);

    const g = svg.append("g").attr("transform", `translate(${margin.left},${margin.top})`);

    const hierData = toHierarchy(tree);
    const root = d3.hierarchy(hierData);

    function setRadius(node: d3.HierarchyNode<HierarchyNode>, y0: number) {
      (node as any).radius = y0 + (node.data.branchLength || 0);
      if (node.children) node.children.forEach((child) => setRadius(child, (node as any).radius));
    }
    setRadius(root, 0);

    let maxRadius = 0;
    root.each((node) => { const r = (node as any).radius as number; if (r > maxRadius) maxRadius = r; });

    const xScale = d3.scaleLinear().domain([0, maxRadius || 1]).range([0, innerWidth]);
    const cluster = d3.cluster<HierarchyNode>().size([innerHeight, innerWidth]);
    cluster(root);

    g.selectAll(".link")
      .data(root.links())
      .join("path")
      .attr("fill", "none")
      .attr("stroke", "#cbd5e1")
      .attr("stroke-width", 1.5)
      .attr("d", (d) => {
        const sx = xScale((d.source as any).radius);
        const sy = d.source.x!;
        const tx = xScale((d.target as any).radius);
        const ty = d.target.x!;
        return `M${sx},${sy}V${ty}H${tx}`;
      });

    const node = g
      .selectAll(".node")
      .data(root.descendants())
      .join("g")
      .attr("transform", (d) => `translate(${xScale((d as any).radius)},${d.x})`);

    node.filter((d) => !d.children)
      .append("circle")
      .attr("r", 4)
      .attr("fill", (d) => getSpeciesColor(getSpeciesFromLeaf(d.data.name)));

    node.filter((d) => !d.children)
      .append("text")
      .attr("dx", 8)
      .attr("dy", 3)
      .attr("font-size", "11px")
      .attr("fill", "#374151")
      .text((d) => d.data.name.replace(/\|/g, ":"));

    node.filter((d) => !!d.children)
      .append("circle")
      .attr("r", 2)
      .attr("fill", "#94a3b8");
  }, [tree]);

  return (
    <div ref={containerRef} className="overflow-x-auto">
      <svg ref={svgRef} />
    </div>
  );
}
