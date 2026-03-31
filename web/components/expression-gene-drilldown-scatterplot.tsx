import * as React from 'react';
import { useEffect, useRef, useState, useMemo } from "react";
import Box from '@mui/material/Box';
import Button from '@mui/material/Button';
import Typography from '@mui/material/Typography';
import Chip from '@mui/material/Chip';
import FormControlLabel from '@mui/material/FormControlLabel';
import Switch from '@mui/material/Switch';
import Tooltip from '@mui/material/Tooltip';
import html2canvas from "html2canvas";
import CameraAltIcon from '@mui/icons-material/CameraAlt';
import FileDownloadIcon from '@mui/icons-material/FileDownload';
import * as d3 from "d3";

type ScatterPlot = {
    cluster_id: string | null;
    barcode: string | null;
    x: number | null;
    y: number | null;
    expression: number;
}

// Bright distinct colors for clusters
const CLUSTER_COLORS = [
    "#e6194B", "#3cb44b", "#ffe119", "#4363d8", "#f58231",
    "#911eb4", "#42d4f4", "#f032e6", "#bfef45", "#fabed4",
    "#469990", "#dcbeff", "#9A6324", "#fffac8", "#800000",
    "#aaffc3", "#808000", "#ffd8b1", "#000075", "#a9a9a9"
];

export default function GeneDrillUMAP({ scatterPlot, geneName }: { scatterPlot: ScatterPlot[], geneName: string }) {
    const chartRef = useRef<SVGSVGElement | null>(null);
    const [selectedClusters, setSelectedClusters] = useState<Set<string>>(new Set());
    const [colorByCluster, setColorByCluster] = useState(false);

    // Get unique cluster IDs
    const uniqueClusters = useMemo(() => {
        const clusters = new Set<string>();
        scatterPlot.forEach(d => {
            if (d.cluster_id) clusters.add(d.cluster_id);
        });
        return Array.from(clusters).sort();
    }, [scatterPlot]);

    // Create cluster color scale
    const clusterColorScale = useMemo(() => {
        return d3.scaleOrdinal<string>()
            .domain(uniqueClusters)
            .range(CLUSTER_COLORS);
    }, [uniqueClusters]);

    // Toggle cluster selection
    const toggleCluster = (clusterId: string) => {
        setSelectedClusters(prev => {
            const newSet = new Set(prev);
            if (newSet.has(clusterId)) {
                newSet.delete(clusterId);
            } else {
                newSet.add(clusterId);
            }
            return newSet;
        });
    };

    // Select all / clear all
    const selectAllClusters = () => setSelectedClusters(new Set(uniqueClusters));
    const clearAllClusters = () => setSelectedClusters(new Set());

    const downloadJSON = () => {
        const dataStr = JSON.stringify(scatterPlot, null, 2);
        const blob = new Blob([dataStr], { type: "application/json" });
        const url = URL.createObjectURL(blob);

        const a = document.createElement("a");
        a.href = url;
        a.download = `${geneName}_UMAP.json`;
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        URL.revokeObjectURL(url);
    }

    const downloadCSV = () => {
        if (scatterPlot.length === 0) return;
        const headers = Object.keys(scatterPlot[0]).join(",") + "\n";
        const rows = scatterPlot.map(row =>
            Object.values(row).map(value => `"${value}"`).join(",")
        ).join("\n");
        const csvString = headers + rows;
        const blob = new Blob([csvString], { type: "text/csv" });
        const url = URL.createObjectURL(blob);
        const a = document.createElement("a");
        a.href = url;
        a.download = `${geneName}_UMAP.csv`;
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        URL.revokeObjectURL(url);
    }

    const downloadChartAsPNG = () => {
        const divElement = document.getElementById("drilldown-scatterplot");
        if (!divElement) return;

        html2canvas(divElement, {
            allowTaint: true,
            useCORS: true,
            logging: true,
        }).then((canvas) => {
            const link = document.createElement("a");
            link.href = canvas.toDataURL("image/png");
            link.download = `${geneName}_UMAP.png`;
            document.body.appendChild(link);
            link.click();
            document.body.removeChild(link);
        }).catch((error) => {
            console.error("Error capturing screenshot:", error);
        });
    };

    useEffect(() => {
        if (scatterPlot) {
            if (!chartRef.current) return;
            const margin = { top: 20, right: 30, bottom: 30, left: 60 };
            const container = chartRef.current.getBoundingClientRect();
            const width = container.width - margin.left - margin.right;
            const height = container.height - margin.top - margin.bottom;

            const legendContainer = document.getElementById("scale");
            if (legendContainer) {
                legendContainer.innerHTML = '';
            }
            const legendWidth = 30;
            const legendHeight = (legendContainer ? legendContainer.clientHeight : 0) - margin.top - margin.bottom;
            const expressionExtent = d3.extent(scatterPlot, (d: ScatterPlot) => d.expression) as [number, number];
            const expressionColorScale = d3.scaleLinear<string>()
                .domain([
                    expressionExtent[0],
                    expressionExtent[0] + (expressionExtent[1] - expressionExtent[0]) * 0.25,
                    expressionExtent[0] + (expressionExtent[1] - expressionExtent[0]) * 0.5,
                    expressionExtent[0] + (expressionExtent[1] - expressionExtent[0]) * 0.75,
                    expressionExtent[1]
                ])
                .range(["#82f584", "#61b863", "#427a43", "#244224", "#0f1c0f"]);

            // Filter data based on selected clusters
            const filteredData = selectedClusters.size === 0
                ? scatterPlot
                : scatterPlot.filter(d => d.cluster_id && selectedClusters.has(d.cluster_id));

            // Determine color function based on mode
            const getColor = (d: ScatterPlot) => {
                if (colorByCluster) {
                    return d.cluster_id ? clusterColorScale(d.cluster_id) : '#999';
                }
                return expressionColorScale(d.expression);
            };

            const tickValues = [expressionExtent[0],
            expressionExtent[0] + (expressionExtent[1] - expressionExtent[0]) * 0.25,
            expressionExtent[0] + (expressionExtent[1] - expressionExtent[0]) * 0.5,
            expressionExtent[0] + (expressionExtent[1] - expressionExtent[0]) * 0.75,
            expressionExtent[1]];

            const yScaleLegend = d3.scaleLinear()
                .domain([expressionExtent[0], expressionExtent[1]])
                .range([legendHeight, 0]);

            d3.select(chartRef.current).selectAll("*").remove();

            const svg = d3
                .select(chartRef.current)
                .attr("width", width)
                .attr("height", height)
                .append("g")
                .attr("transform", `translate(${margin.left},${margin.top})`);
            const xScale = d3
                .scaleLinear()
                .domain(d3.extent(scatterPlot, (d: ScatterPlot) => d.x as number) as [number, number])
                .range([20, width - 20]);
            const yScale = d3
                .scaleLinear()
                .domain(d3.extent(scatterPlot, (d: ScatterPlot) => {
                    return d.y
                }
                ) as [number, number])
                .range([height - 20, 20]);
            const xAxis = d3.axisBottom(xScale);
            const yAxis = d3.axisLeft(yScale);



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

            const highlight = (event: any, d: ScatterPlot) => {
                if (!d) return;
                const selectedLabel = d.cluster_id;

                d3.selectAll(".dot")
                    .filter((dot: any) => (dot as ScatterPlot).cluster_id === selectedLabel)
                    .transition()
                    .duration(200)
                    .style("fill", (dot: any) => getColor(dot as ScatterPlot))
                    .style("filter", "drop-shadow(6px 9px 6px rgba(0, 0, 0, 0.8))")
                    .style("stroke", "black")
                    .style("stroke-width", 2)
                    .attr("r", 10);

                d3.selectAll(".dot")
                    .filter((dot: any) => (dot as ScatterPlot).cluster_id !== selectedLabel)
                    .transition()
                    .duration(200)
                    .style("fill", "lightgrey")
                    .style("stroke", "black")
                    .style("stroke-width", 2)
                    .attr("r", 3);

                d3.select("#scatter-plot-cluster-id")
                    .text("Cell Type (Cluster ID): " + selectedLabel);
            };

            const doNotHighlight = (event: any, d: ScatterPlot) => {
                if (!d) return;

                d3.selectAll(".dot")
                    .transition()
                    .duration(200)
                    .style("stroke", "none")
                    .style("stroke-width", 0)
                    .style("fill", (dot: any) => getColor(dot as ScatterPlot))
                    .style("stroke", "black")
                    .attr("r", 6)
                    .style("stroke-width", 2)
                    .style("filter", "none");

                d3.select("#scatter-plot-cluster-id")
                    .text("");
            };

            // Use filtered data for rendering
            svg.selectAll("circle")
                .data(filteredData)
                .enter()
                .append("circle")
                .attr("class", (d) => `dot ${d.cluster_id}`)
                .attr("cx", (d) => xScale(d.x ? d.x : 0))
                .attr("cy", (d) => yScale(d.y ? d.y : 0))
                .attr("r", 6)
                .style("fill", (d) => getColor(d))
                .style("stroke", "black")
                .style("stroke-width", 1)
                .style("opacity", 0.8)
                .on("mouseover", function (event, d) { highlight(event, d); })
                .on("mouseleave", function (event, d) { doNotHighlight(event, d); });

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

            //#82f584", "#61b863", "#427a43", "#244224", "#0f1c0f
            const legendSvg = d3.select("#scale")
                .append("svg")
                .attr("width", legendWidth)
                .attr("height", legendHeight);

            const defs = legendSvg.append("defs");
            const linearGradient = defs.append("linearGradient")
                .attr("id", "grad1")
                .attr("x1", "0%")
                .attr("y1", "100%")
                .attr("x2", "0%")
                .attr("y2", "0%");

            linearGradient.selectAll("stop")
                .data([
                    { offset: "0%", color: "#82f584" },
                    { offset: "25%", color: "#61b863" },
                    { offset: "50%", color: "#427a43" },
                    { offset: "75%", color: "#244224" },
                    { offset: "100%", color: "#0f1c0f" }
                ])
                .enter().append("stop")
                .attr("offset", d => d.offset)
                .attr("stop-color", d => d.color);

            legendSvg.append('rect')
                .attr('x', 0)
                .attr('y', 0)
                .attr('width', legendWidth)
                .attr('height', legendHeight)
                .attr('fill', 'url(#grad1)');

            legendSvg.selectAll(".tick")
                .data(tickValues)
                .enter()
                .append("line")
                .attr("class", "tick")
                .attr("x1", 0)
                .attr("x2", 10)
                .attr("y1", d => yScaleLegend(d))
                .attr("y2", d => yScaleLegend(d))
                .attr("stroke", "black")
                .attr("stroke-width", 1);

            legendSvg.selectAll(".tick-label")
                .data(tickValues)
                .enter()
                .append("text")
                .attr("class", "tick-label")
                .attr("x", 10)
                .attr("y", d => yScaleLegend(d))
                .attr("dy", "0.32em")
                .attr("text-anchor", "left")
                .attr("font-size", "12px")
                .attr("fill", "white")
                .text(d => d);

        }

    }, [scatterPlot, selectedClusters, colorByCluster, clusterColorScale])

    // Truncate cluster name for display
    const truncateClusterName = (name: string, maxLen: number = 15) => {
        if (name.length <= maxLen) return name;
        return name.substring(0, maxLen - 2) + '..';
    };

    return (
        <Box sx={{ height: '100%' }}>
            <div style={{ marginLeft: '10px', margin: '10px', padding: '10px', display: 'flex', alignItems: 'center', flexWrap: 'wrap', gap: '10px' }}>
                <Button
                    onClick={() => { downloadJSON() }}
                    variant="outlined"
                    size="small"
                >
                    JSON <FileDownloadIcon />
                </Button>
                <Button
                    variant="outlined"
                    onClick={() => { downloadCSV() }}
                    size="small"
                >
                    CSV <FileDownloadIcon />
                </Button>
                <Button
                    variant="outlined"
                    onClick={() => { downloadChartAsPNG() }}
                    size="small"
                >
                    <CameraAltIcon /> <FileDownloadIcon />
                </Button>
                <Tooltip
                    title="Toggle to color cells by their cluster identity instead of expression level. Helps visualize cluster boundaries and cell type distributions."
                    arrow
                    placement="bottom"
                >
                    <FormControlLabel
                        control={
                            <Switch
                                checked={colorByCluster}
                                onChange={(e) => setColorByCluster(e.target.checked)}
                                size="small"
                            />
                        }
                        label="Color by Cluster"
                        sx={{ ml: 2 }}
                    />
                </Tooltip>
            </div>

            {/* Cluster Filter Legend */}
            <Box sx={{
                mx: 2,
                mb: 1,
                p: 1.5,
                border: '1px solid #e0e0e0',
                borderRadius: 2,
                backgroundColor: '#fafafa'
            }}>
                <Box sx={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', mb: 1 }}>
                    <Typography variant="subtitle2" sx={{ fontWeight: 'bold' }}>
                        Filter by Cluster {selectedClusters.size > 0 && `(${selectedClusters.size} selected)`}
                    </Typography>
                    <Box>
                        <Button size="small" onClick={selectAllClusters} sx={{ mr: 1 }}>Select All</Button>
                        <Button size="small" onClick={clearAllClusters}>Clear</Button>
                    </Box>
                </Box>
                <Box sx={{ display: 'flex', flexWrap: 'wrap', gap: 0.5 }}>
                    {uniqueClusters.map((cluster) => (
                        <Tooltip key={cluster} title={cluster} arrow placement="top">
                            <Chip
                                label={truncateClusterName(cluster)}
                                size="small"
                                onClick={() => toggleCluster(cluster)}
                                sx={{
                                    backgroundColor: selectedClusters.size === 0 || selectedClusters.has(cluster)
                                        ? clusterColorScale(cluster)
                                        : '#e0e0e0',
                                    color: selectedClusters.size === 0 || selectedClusters.has(cluster)
                                        ? '#fff'
                                        : '#666',
                                    fontWeight: selectedClusters.has(cluster) ? 'bold' : 'normal',
                                    border: selectedClusters.has(cluster) ? '2px solid #333' : '1px solid #ccc',
                                    '&:hover': {
                                        backgroundColor: clusterColorScale(cluster),
                                        opacity: 0.8
                                    },
                                    cursor: 'pointer',
                                    fontSize: '0.75rem'
                                }}
                            />
                        </Tooltip>
                    ))}
                </Box>
            </Box>
            <Box
                sx={{
                    display: 'flex',
                    width: '100%',
                    height: '800px',
                    flexDirection: { xs: 'column', md: 'row' },
                }}
                id='drilldown_scatterplot'
            >
                <Box
                    sx={{
                        flex: 8,
                        display: 'flex',
                        flexDirection: 'column',
                        justifyContent: 'center',
                    }}

                >
                    <Typography
                        variant="h6"
                        sx={{ textAlign: 'center', marginTop: '20px', position: 'relative' }}
                    >
                        UMAP Projection across cell types (Clusters) for <b>{geneName}</b>
                        <Typography
                            id="scatter-plot-cluster-id"
                            sx={{
                                fontWeight: 'bold',
                                position: 'absolute',
                                left: '40%',
                            }}
                        >
                            {' '}
                        </Typography>
                    </Typography>
                    <svg
                        style={{ width: '100%', height: '100%' }}
                        id="chart-container"
                        ref={chartRef}
                    />
                </Box>
                <Box
                    sx={{
                        flex: 1,
                        display: 'flex',
                        flexDirection: 'column',
                        padding: '10px',
                        width: '20px',
                        height: '100%'
                    }}
                    id='scale'
                >

                </Box>

                {/* <Box
                    sx={{
                        flex: 1,
                        display: 'flex',
                        flexDirection: 'column',
                        // padding: '10px',
                    }}
                    id='drilldown-scatterplot-legend'
                >
                <Typography variant="h6" sx={{ textAlign: 'center', marginBottom: '10px' }}>
                    Cell Type
                </Typography>
                {clusterId}
                </Box> */}
            </Box>
        </Box>
    );
}
