import * as React from 'react';
import { useEffect, useRef, useState } from "react";
import { Database } from "@/lib/database.types";
import { createClientSupabaseClient } from "@/lib/supabase/client";
import * as d3 from "d3";
import { Container } from 'postcss';

interface Point {
    x: number;
    y: number;
    cluster: string;
    expression_level: number
}

interface Scatterplot {
    [key: string]: Point[];
}

export default function ExpressionGeneLevelScatterPlot({ scatterplot, show_scatterplot, drill_down_gene }: { scatterplot: Scatterplot, show_scatterplot: Boolean, drill_down_gene: String }) {
    const chartRef = useRef<SVGSVGElement | null>(null);

    useEffect(() => {
        if (show_scatterplot) {
            let points: Point[] = [];
            Object.keys(scatterplot).forEach((cluster) => {
                scatterplot[cluster].forEach((point) => {
                    points.push({ x: point.x, y: point.y, cluster: cluster, expression_level: point.expression_level });
                });
            });

            if (!chartRef.current) return;
            const margin = { top: 20, right: 30, bottom: 30, left: 60 };
            // const container = chartRef.current.getBoundingClientRect();
            const width = 900 - margin.left - margin.right;
            const height = 600 - margin.top - margin.bottom;

            d3.select(chartRef.current).selectAll("*").remove();
            if (scatterplot) {
                const svg = d3
                    .select(chartRef.current)
                    .attr("width", width)
                    .attr("height", height)
                    .append("g")
                    .attr("transform", `translate(${margin.left},${margin.top})`);
                const xScale = d3
                    .scaleLinear()
                    .domain(
                        (d3.extent(points, (d: { x: number; y: number; cluster: string }) => d.x) as [number, number] || [0, 0]))
                    .range([20, width - 20]);
                const yScale = d3
                    .scaleLinear()
                    .domain(d3.extent(points, (d) => {
                        return d.y
                    }
                    ) as [number, number])
                    .range([height - 20, 20]);
                const xAxis = d3.axisBottom(xScale);
                const yAxis = d3.axisLeft(yScale);
                const expressionExtent = d3.extent(points, (d) => d.expression_level) as [number, number];
                const colorScale = d3.scaleLinear<string>()
                    .domain([
                        expressionExtent[0],
                        expressionExtent[0] + (expressionExtent[1] - expressionExtent[0]) * 0.25,
                        expressionExtent[0] + (expressionExtent[1] - expressionExtent[0]) * 0.5,
                        expressionExtent[0] + (expressionExtent[1] - expressionExtent[0]) * 0.75,
                        expressionExtent[1]
                    ])
                    .range(["#82f584", "#61b863", "#427a43", "#244224", "#0f1c0f"]);
                
                svg.append("g")
                    .attr("class", "x axis")
                    .attr("transform", `translate(0,${height})`)
                    .call(xAxis)
                    .selectAll("path, line, text")
                    .style("stroke", "black")
                    .style("fill", "black");
                
                svg.append("g")
                    .attr("class", "y axis")
                    .call(yAxis)
                    .selectAll("path, line, text")
                    .style("stroke", "black")
                    .style("fill", "black");
                
                svg.append("g")
                    .attr("transform", `translate(0,${height})`)
                    .call(d3.axisBottom(xScale));
                svg.append("g").call(d3.axisLeft(yScale));
                
                const tooltip = d3
                    .select("body")
                    .append("div")
                    .attr("class", "tooltip")
                    .style("position", "absolute")
                    .style("visibility", "hidden")
                    .style("background-color", "#fff")
                    .style("border", "1px solid #ccc")
                    .style("padding", "10px")
                    .style("border-radius", "5px")
                    .style("box-shadow", "0px 0px 10px rgba(0, 0, 0, 0.1)")
                    .style("color", "black");

                const highlight = (event: any, d: Point) => {
                    if (!d) return;
                    const selectedLabel = d.cluster;

                    const doNotHighlight = (event: any, d: Point) => {
                        tooltip.style("visibility", "hidden");
                        d3.selectAll(`.${d.cluster}`)
                            .transition()
                            .duration(200)
                            .attr("r", 5);
                    };

                    d3.selectAll(".dot")
                        .filter((dot: any) => dot.cluster === selectedLabel)
                        .transition()
                        .duration(200)
                        .style("fill", (d) => colorScale((d as Point).expression_level)) 
                        .style("filter", "drop-shadow(6px 9px 6px rgba(0, 0, 0, 0.8))") 
                        .style("stroke", "black")
                        .style("stroke-width", 2)
                        .attr("r", 10);

                    d3.selectAll(".dot")
                        .filter((dot: any) => dot.cluster !== selectedLabel)
                        .transition()
                        .duration(200)
                        .style("fill", "lightgrey")
                        .style("stroke", "black")
                        .style("stroke-width", 2)
                        .attr("r", 3);

                    d3.select("#scatter-plot-cluster-id")
                        .text("Cell Type (Cluster ID): " + selectedLabel); 
                };

                const doNotHighlight = (event: any, d: Point) => {
                    if (!d) return;

                    d3.selectAll(".dot")
                        .transition()
                        .duration(200)
                        .style("stroke", "none")
                        .style("stroke-width", 0)
                        .style("fill", (d) => colorScale((d as Point).expression_level))
                        .style("stroke", "black")
                        .attr("r", 6)
                        .style("stroke-width", 2)
                        .style("filter", "none");
                    
                    d3.select("#scatter-plot-cluster-id")
                        .text("");
                };

                svg.selectAll("circle")
                    .data(points)
                    .enter()
                    .append("circle")
                    .attr("class", (d) => `dot ${d.cluster}`)
                    .attr("cx", (d) => xScale(d.x))
                    .attr("cy", (d) => yScale(d.y))
                    .attr("r", 6)
                    .style("fill", (d) => colorScale(d.expression_level))
                    .style("stroke", "black")
                    .style("stroke-width", 2)
                    .style("opacity", 0.8)
                    .on("mouseover", function(event, d) { highlight(event, d);})
                    .on("mouseleave", function(event, d) { doNotHighlight(event, d);});

                svg.append("text")
                    .attr("class", "x label")
                    .attr("text-anchor", "middle")
                    .attr("x", width / 2)
                    .attr("y", height + margin.bottom - 5)
                    .style("font-size", "16px")
                    .style("fill", "black")
                    .text("C2");

                svg.append("text")
                    .attr("class", "y label")
                    .attr("text-anchor", "middle")
                    .attr("transform", `rotate(-90)`)
                    .attr("x", -height / 2)
                    .attr("y", -margin.left + 15)
                    .style("font-size", "16px")
                    .style("fill", "black")
                    .text("C1");

                const legendValues = [
                        expressionExtent[0],
                        expressionExtent[0] + (expressionExtent[1] - expressionExtent[0]) * 0.25,
                        expressionExtent[0] + (expressionExtent[1] - expressionExtent[0]) * 0.5,
                        expressionExtent[0] + (expressionExtent[1] - expressionExtent[0]) * 0.75,
                        expressionExtent[1]
                ];
                const legend = svg.append("g")
                    .attr("class", "color-legend")
                    .attr("transform", `translate(${width - 100}, 50)`);
                legend.append("text")
                    .attr("x", 0)
                    .attr("y", -10)
                    .text("Counts")  
                    .style("font-size", "16px")  
                    .style("font-weight", "bold")
                    .style("fill", "black");
                legend.selectAll("rect")
                    .data(legendValues)
                    .enter()
                    .append("rect")
                    .attr("x", 0)
                    .attr("y", (d, i) => i * 25) 
                    .attr("width", 20)
                    .attr("height", 20)
                    .style("fill", d => colorScale(d));
                legend.selectAll("text")
                    .data(legendValues)
                    .enter()
                    .append("text")
                    .attr("x", 30)
                    .attr("y", (d, i) => i * 25 + 15)
                    .text(d => d.toFixed(2))
                    .style("font-size", "14px")
                    .style("alignment-baseline", "middle")
                    .style("fill", "black");
            }
        }
        else {
            d3.select(chartRef.current).selectAll("*").remove();
        }

    }, [scatterplot, show_scatterplot])

    return (
        show_scatterplot && (
            <div>
                <h1 style={{ textAlign: "center", marginTop:"20px", position:"relative"}}>
                    UMAP Projection across cell types (Clusters) for <b>{drill_down_gene}</b>
                    <h1 id="scatter-plot-cluster-id" style={{fontWeight:"bold", position: "absolute", left:"40%"}}>' '</h1>
                </h1>
                <svg
                    style={{ width: '100%', height: '100%' }}
                    id="chart-container"
                    ref={chartRef}
                />

            </div>
        )
    );

}