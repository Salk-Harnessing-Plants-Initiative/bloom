import React, { useEffect, useRef, useState } from "react";
import Box from '@mui/material/Box';
import Button from '@mui/material/Button';
import FileDownloadIcon from '@mui/icons-material/FileDownload';
import html2canvas from "html2canvas";
import CameraAltIcon from '@mui/icons-material/CameraAlt';
import TouchAppIcon from '@mui/icons-material/TouchApp';
import { Alert, Tooltip } from '@mui/material';
import * as d3 from "d3";


type GeneNames = {
    gene_number: number,
    gene_name: string
}

type DotPlotData = {
    genes: string[],
    clusters: string[],
    expression: {
        gene: string,
        cluster: string,
        avg_value: number;
        percent_expressed: number;
        expressed_cells: number;
        total_cells: number;
    }[]
}

type GeneData = {
    gene_id: number;
    gene_name: string;
    counts: [{
        key: number
        value: number
    }];
    data: {
        cluster_id: string | null;
        barcode: string | null;
        cell_number: number;
        x: number | null;
        y: number | null;
    }[] | null;
}

export default function ExpressionMultiGeneDotPlot({ input_array, setDrillDownGene }: { input_array: Record<string, GeneData>, setDrillDownGene: (gene_name: string) => void }) {
    const chartRef = useRef<HTMLDivElement | null>(null);
    const colorLegendRef = useRef<HTMLDivElement | null>(null);
    const percentLegendRef = useRef<HTMLDivElement | null>(null);

    const [chartHeight, setChartHeight] = useState(100);
    const [data, setData] = useState<DotPlotData>({
        genes: [],
        clusters: [],
        expression: []
    });
    const tooltipRef = useRef<HTMLDivElement | null>(null);

    const downloadJSON = () => {
        const dataStr = JSON.stringify(data, null, 2);
        const blob = new Blob([dataStr], { type: "application/json" });
        const url = URL.createObjectURL(blob);

        const a = document.createElement("a");
        a.href = url;
        a.download = "expression_across_clusters.json";
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        URL.revokeObjectURL(url);
    }

    const downloadCSV = () => {
        if (!data.genes.length || !data.clusters.length || !data.expression.length) return;
        const headers = ["Gene", "Cluster", "Expression"].join(",") + "\n";
        const rows = data.genes.map((gene, index) => {
            return `"${gene}","${data.clusters[index]}",${JSON.stringify(data.expression[index])}`;
        }).join("\n");
        const csvString = headers + rows;
        const blob = new Blob([csvString], { type: "text/csv" });
        const url = URL.createObjectURL(blob);
        const a = document.createElement("a");
        a.href = url;
        a.download = "expression_across_clusters.csv";
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        URL.revokeObjectURL(url);
    }

    const downloadChartAsPNG = () => {
    const divElement = document.getElementById("image-dot-plot");
    if (!divElement) return;

    html2canvas(divElement, {
        allowTaint: true,
        useCORS: true,
        logging: true,
    }).then((canvas) => {
        const link = document.createElement("a");
        link.href = canvas.toDataURL("image/png");
        link.download = "screenshot.png";
        document.body.appendChild(link);
        link.click();
        document.body.removeChild(link);
    }).catch((error) => {
        console.error("Error capturing screenshot:", error);
    });
    };

    useEffect(() => {
        const geneNames: any[] = Object.keys(input_array).map((gene_name: string) => ({ gene_name: gene_name }));
        const transformData = (input_array: Record<string, GeneData>): DotPlotData => {
            const genes: string[] = [];
            const clusters: string[] = [];
            const expression: {
                gene: string;
                cluster: string;
                avg_value: number;
                percent_expressed: number;
                expressed_cells: number;
                total_cells: number;
            }[] = [];

            // 1: computing avg expression of gene across clusters 
            // 2: % if cells expressed in each cluster.

            Object.values(input_array).forEach((geneData) => {
                const { gene_name, counts, data } = geneData;
                if (!genes.includes(gene_name)) {
                    genes.push(gene_name);
                }

                const geneClusterMap = new Map<string, { total: number; count: number; expressedCells: Set<string> }>();
                // counts is now an object {cellId: expressionValue, ...} not an array
                const countsEntries = Object.entries(counts);
                countsEntries.forEach(([cellId, value]) => {
                    const clusterData = data?.find((cell) => cell.cell_number === Number(cellId));
                    const barcode = clusterData?.barcode;

                    if (clusterData?.cluster_id) {
                        const clusterId = clusterData?.cluster_id.toString();
                        if (!clusters.includes(clusterId)) {
                            clusters.push(clusterId);
                        }
                        const mapKey = `${gene_name}@${clusterId}`;
                        if (!geneClusterMap.has(mapKey)) {
                            geneClusterMap.set(mapKey, { total: 0, count: 0, expressedCells: new Set() });
                        }
                        const clusterStats = geneClusterMap.get(mapKey)!;
                        clusterStats.total += value as number;
                        clusterStats.count += 1;
                        if ((value as number) > 0 && clusterData.barcode) clusterStats.expressedCells.add(clusterData.barcode);
                    }
                });

                geneClusterMap.forEach((value, key) => {
                    const [geneName, clusterId] = key.split('@');
                    const avgExpression = value.total / value.count;
                    const percentExpressed = value.expressedCells.size / countsEntries.length;
                    expression.push({
                        gene: geneName,
                        cluster: clusterId,
                        avg_value: parseFloat(avgExpression.toFixed(2)),
                        percent_expressed: parseFloat(percentExpressed.toFixed(2)),
                        expressed_cells: value.expressedCells.size,
                        total_cells: countsEntries.length
                    });

                });
            });
            return {
                genes,
                clusters,
                expression,
            };
        };

        const dotPlotData = transformData(input_array);
        setData(dotPlotData);

    }, [input_array]);

    // Helper function to truncate long gene names
    const truncateGeneName = (name: string, maxLength: number = 20) => {
        if (name.length <= maxLength) return name;
        return name.substring(0, maxLength - 3) + '...';
    };

    useEffect(() => {
        if (!chartRef.current) return;

        d3.select(chartRef.current).selectAll("*").remove();
        d3.select(colorLegendRef.current).selectAll("*").remove();
        d3.select(percentLegendRef.current).selectAll("*").remove();

        // Dynamic sizing based on data
        const numClusters = data.clusters.length;
        const numGenes = data.genes.length;

        // Fixed cell size for consistent, readable dots
        // This ensures dots are always a good size - container will scroll if needed
        const cellSize = 80; // Larger size per cell for better dot visibility

        const margin = { top: 140, right: 40, bottom: 40, left: 200 };

        // Calculate dimensions based on cell size
        const width = numClusters * cellSize;
        const chartHeight = numGenes * cellSize;

        setChartHeight(chartHeight + margin.top + margin.bottom);

        const totalWidth = width + margin.left + margin.right;
        const totalHeight = chartHeight + margin.top + margin.bottom;

        const svg = d3.select(chartRef.current)
            .append("svg")
            .attr("width", totalWidth)
            .attr("height", totalHeight)
            .append("g")
            .attr("transform", `translate(${margin.left},${margin.top})`);

        // X-axis (Clusters) - positioned at top
        const x = d3.scaleBand().range([0, width]).domain(data.clusters).padding(0.1);
        svg.append("g")
            .attr("class", "x-axis")
            .attr("transform", `translate(0, -10)`)
            .call(d3.axisTop(x).tickSize(0))
            .select(".domain").remove();

        // Style X-axis labels - rotated for better readability
        // Adjust font size based on number of clusters
        const xFontSize = numClusters > 15 ? '10px' : numClusters > 10 ? '11px' : '12px';
        svg.selectAll('.x-axis text')
            .style('font-size', xFontSize)
            .style('font-weight', '500')
            .attr("transform", "rotate(-50)")
            .attr("text-anchor", "start")
            .attr("dx", "0.5em")
            .attr("dy", "0em");

        // X-axis title
        svg.append('text')
            .attr('transform', `translate(${width / 2}, -100)`)
            .style('text-anchor', 'middle')
            .style('font-size', '14px')
            .style('font-weight', 'bold')
            .text('Cluster');

        // Y-axis (Genes)
        const y = d3.scaleBand().range([0, chartHeight]).domain(data.genes).padding(0.1);
        svg.append("g")
            .attr("class", "y-axis")
            .call(d3.axisLeft(y).tickSize(0).tickFormat(d => truncateGeneName(String(d), 22)))
            .select(".domain").remove();

        // Style Y-axis labels - horizontal, clickable with visual feedback
        svg.selectAll('.y-axis text')
            .style('text-anchor', 'end')
            .style('font-size', '11px')
            .style('cursor', 'pointer')
            .style('fill', '#1976d2')
            .attr("dx", "-0.5em")
            .on('mouseover', function (event, d) {
                d3.select(this)
                    .transition()
                    .duration(150)
                    .style('font-weight', 'bold')
                    .style('fill', '#1565c0')
                    .style('text-decoration', 'underline');

                // Show full name in tooltip if truncated
                const fullName = String(d);
                if (fullName.length > 22 && tooltipRef.current) {
                    tooltipRef.current.style.visibility = "visible";
                    tooltipRef.current.innerHTML = `<strong>${fullName}</strong><br><small>Click for detailed view</small>`;
                    tooltipRef.current.style.left = `${event.pageX + 10}px`;
                    tooltipRef.current.style.top = `${event.pageY - 30}px`;
                }
            })
            .on('mouseout', function () {
                d3.select(this)
                    .transition()
                    .duration(150)
                    .style('font-weight', 'normal')
                    .style('fill', '#1976d2')
                    .style('text-decoration', 'none');

                if (tooltipRef.current) {
                    tooltipRef.current.style.visibility = "hidden";
                }
            })
            .on('click', function (event, d) {
                setDrillDownGene(String(d));
            });

        // Y-axis title with click hint
        svg.append('text')
            .attr('transform', `rotate(-90)`)
            .attr('y', -margin.left + 15)
            .attr('x', -chartHeight / 2)
            .style('text-anchor', 'middle')
            .style('font-size', '16px')
            .style('font-weight', 'bold')
            .text('Genes (click for details)');

        const myColor = d3.scaleSequential().interpolator(d3.interpolatePurples).domain([d3.min(data.expression, d => (d.avg_value) * 100) || 0, (d3.max(data.expression, d => (d.avg_value) * 100) || 0)]);

        // Calculate max radius based on cell size (leave some padding)
        const maxRadius = Math.min(x.bandwidth(), y.bandwidth()) / 2 - 3;
        const minRadius = Math.max(6, maxRadius * 0.25); // Minimum 25% of max or 6px

        // Radius scale - larger dots for better visibility
        const radiusScale = d3.scaleLinear()
            .domain([0, 100])
            .range([minRadius, maxRadius]);

        const circles = svg.selectAll()
            .data(data.expression)
            .enter()
            .append("circle")
            .attr("cx", d => (x(d.cluster) ?? 0) + x.bandwidth() / 2)
            .attr("cy", d => (y(d.gene) ?? 0) + y.bandwidth() / 2)
            .attr("r", d => radiusScale(d.percent_expressed * 100))
            .style("fill", d => myColor(d.avg_value * 100))
            .style("opacity", 0.85)
            .style("stroke", "#ccc")
            .style("stroke-width", 0.5)
            .style("cursor", "pointer")
            .on("mouseover", (event, d) => {
                d3.select(event.currentTarget)
                    .transition()
                    .duration(100)
                    .style("opacity", 1)
                    .style("stroke", "#333")
                    .style("stroke-width", 2);

                if (tooltipRef.current) {
                    tooltipRef.current.style.visibility = "visible";
                    tooltipRef.current.innerHTML = `
                        <strong>${d.gene}</strong><br>
                        <span style="color: #aaa">Cluster:</span> ${d.cluster}<br>
                        <span style="color: #aaa">Avg Expression:</span> ${d.avg_value.toFixed(2)}<br>
                        <span style="color: #aaa">% Expressing:</span> ${(d.percent_expressed * 100).toFixed(1)}%<br>
                        <span style="color: #aaa">Cells:</span> ${d.expressed_cells} / ${d.total_cells}
                    `;
                    tooltipRef.current.style.left = `${event.pageX + 15}px`;
                    tooltipRef.current.style.top = `${event.pageY - 10}px`;
                }
            })
            .on("mouseout", (event) => {
                d3.select(event.currentTarget)
                    .transition()
                    .duration(100)
                    .style("opacity", 0.85)
                    .style("stroke", "#ccc")
                    .style("stroke-width", 0.5);

                if (tooltipRef.current) {
                    tooltipRef.current.style.visibility = "hidden";
                }
            })
            .on("click", (event, d) => {
                setDrillDownGene(d.gene);
            });


        const colorLegend = d3.select(colorLegendRef.current)
            .append("svg")
            .attr("width", 150)
            .attr("height", 80)
        const gradient = colorLegend.append("defs")
            .append("linearGradient")
            .attr("id", "legend-gradient")
            .attr("x1", "0%")
            .attr("x2", "100%")
            .attr("y1", "0%")
            .attr("y2", "0%");
        gradient.selectAll("stop")
            .data([
                { offset: "0%", color: myColor(myColor.domain()[0]) },
                { offset: "100%", color: myColor(myColor.domain()[1]) }
            ])
            .enter().append("stop")
            .attr("offset", d => d.offset)
            .attr("stop-color", d => d.color);
        colorLegend.append("rect")
            .attr("x", 0)
            .attr("y", 25)
            .attr("width", 150)
            .attr("height", 10)
            .style("fill", "url(#legend-gradient)");
        colorLegend.append("text")
            .attr("x", 0)
            .attr("y", 50)
            .style("font-size", "18px")
            .text(Math.floor(d3.min(data.expression, d => d.avg_value) ?? 0).toFixed(1));
        colorLegend.append("text")
            .attr("x", 150)
            .attr("y", 50)
            .style("font-size", "18px")
            .style("text-anchor", "end")
            .text(Math.ceil(d3.max(data.expression, d => d.avg_value) ?? 0).toFixed(1));

        // Fixed legend radius scale for consistent display
        const legendRadiusScale = d3.scaleLinear().domain([0, 100]).range([5, 20]);

        const sizeLegend = d3.select(percentLegendRef.current)
            .append("svg")
            .attr("width", 180)
            .attr("height", 80);

        // Legend circles with fixed sizes
        const legendData = [
            { percent: 20, cx: 25 },
            { percent: 40, cx: 70 },
            { percent: 80, cx: 130 }
        ];

        legendData.forEach(item => {
            sizeLegend.append("circle")
                .attr("cx", item.cx)
                .attr("cy", 30)
                .attr("r", legendRadiusScale(item.percent))
                .style("fill", "#9e9e9e")
                .style("stroke", "#666")
                .style("stroke-width", 0.5);

            sizeLegend.append("text")
                .attr("x", item.cx)
                .attr("y", 60)
                .style("font-size", "11px")
                .style("text-anchor", "middle")
                .text(item.percent + "%");
        });

        function sortByExpression() {
            const sortedGenes = [...data.genes].sort((a, b) => {
                const aValue = data.expression.find(d => d.gene === a)?.avg_value || 0;
                const bValue = data.expression.find(d => d.gene === b)?.avg_value || 0;
                return bValue - aValue;
            });

            y.domain(sortedGenes);

            svg.select(".y-axis")
                .transition()
                .duration(1000)
                .call(d3.axisLeft(y).tickSize(0).tickFormat(d => truncateGeneName(String(d), 22)) as any);

            circles.transition()
                .duration(1000)
                .attr("cy", d => (y(d.gene) ?? 0) + y.bandwidth() / 2);
        }

        function sortByGeneName() {
            const sortedGenes = [...data.genes].sort((a, b) => a.localeCompare(b));

            y.domain(sortedGenes);

            svg.select(".y-axis")
                .transition()
                .duration(1000)
                .call(d3.axisLeft(y).tickSize(0).tickFormat(d => truncateGeneName(String(d), 22)) as any);

            circles.transition()
                .duration(1000)
                .attr("cy", d => (y(d.gene) ?? 0) + y.bandwidth() / 2);
        }

        d3.select("#sortByCounts").on("click", sortByExpression);
        d3.select("#sortByGenes").on("click", sortByGeneName);

    }, [data]);



    return (
        <>
            <Box sx={{ padding: '18px', display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: 2 }} >
                <Box sx={{ display: 'flex', gap: 1 }}>
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
                        <CameraAltIcon/> <FileDownloadIcon />
                    </Button>
                </Box>
                <Tooltip title="Click on any gene name or dot to see detailed expression analysis" arrow>
                    <Alert
                        severity="info"
                        icon={<TouchAppIcon />}
                        sx={{
                            py: 0,
                            px: 2,
                            fontSize: '0.85rem',
                            '& .MuiAlert-message': { padding: '4px 0' }
                        }}
                    >
                        Click on gene names or dots for detailed cluster analysis
                    </Alert>
                </Tooltip>
            </Box>
            <Box id='image-dot-plot'>
                <div
                    ref={tooltipRef}
                    style={{
                        position: "fixed",
                        visibility: "hidden",
                        backgroundColor: "rgba(30, 30, 30, 0.95)",
                        color: "white",
                        padding: "10px 14px",
                        borderRadius: "8px",
                        fontSize: "13px",
                        pointerEvents: "none",
                        zIndex: 1000,
                        boxShadow: "0 4px 12px rgba(0,0,0,0.3)",
                        lineHeight: 1.5,
                        maxWidth: "280px"
                    }}
                ></div>
                <div style={{ display: "flex", flexDirection: "row", justifyContent: "center", gap: "40px", marginBottom: "10px" }}>
                    <div style={{ display: "flex", flexDirection: "column", alignItems: "center" }}>
                        <span style={{ fontSize: '14px', fontWeight: 500, marginBottom: '5px' }}>Avg. Expression Levels</span>
                        <div ref={colorLegendRef} id="colorLegend"></div>
                    </div>
                    <div style={{ display: "flex", flexDirection: "column", alignItems: "center" }}>
                        <span style={{ fontSize: '14px', fontWeight: 500, marginBottom: '5px' }}>% of Cells Expressing</span>
                        <div ref={percentLegendRef} id="percentLegend"></div>
                    </div>
                </div>
                <div style={{
                    width: '100%',
                    maxHeight: '700px',
                    overflow: 'auto',
                    border: '1px solid #e0e0e0',
                    borderRadius: '8px',
                    backgroundColor: '#fafafa'
                }}>
                    <div
                        ref={chartRef}
                        style={{
                            display: 'inline-block',
                            minWidth: 'fit-content'
                        }}
                    ></div>
                </div>
            </Box>
        </>
    );
}
