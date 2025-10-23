import * as React from 'react';
import { useEffect, useRef, useState } from "react";
import Box from '@mui/material/Box';
import Button from '@mui/material/Button';
import Typography from '@mui/material/Typography';
import html2canvas from "html2canvas";
import CameraAltIcon from '@mui/icons-material/CameraAlt';
import FileDownloadIcon from '@mui/icons-material/FileDownload';
import * as d3 from "d3";

type ScatterPlot = {
    cluster_id: number | null;
    barcode: string | null;
    x: number | null;
    y: number | null;
    expression: number;
}

export default function GeneDrillUMAP({ scatterPlot, geneName }: { scatterPlot: ScatterPlot[], geneName: string }) {
    const chartRef = useRef<SVGSVGElement | null>(null);
    const [clusterId, setClusterId] = useState<(string | null)[]>([]);

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

            setClusterId(scatterPlot.map(item => item.cluster_id?.toString() || null))
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
            const colorScale = d3.scaleLinear<string>()
                .domain([
                    expressionExtent[0],
                    expressionExtent[0] + (expressionExtent[1] - expressionExtent[0]) * 0.25,
                    expressionExtent[0] + (expressionExtent[1] - expressionExtent[0]) * 0.5,
                    expressionExtent[0] + (expressionExtent[1] - expressionExtent[0]) * 0.75,
                    expressionExtent[1]
                ])
                .range(["#82f584", "#61b863", "#427a43", "#244224", "#0f1c0f"]);

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
                    .style("fill", (d) => colorScale((d as ScatterPlot).expression))
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
                    .style("fill", (d) => colorScale((d as ScatterPlot).expression))
                    .style("stroke", "black")
                    .attr("r", 6)
                    .style("stroke-width", 2)
                    .style("filter", "none");

                d3.select("#scatter-plot-cluster-id")
                    .text("");
            };

            svg.selectAll("circle")
                .data(scatterPlot)
                .enter()
                .append("circle")
                .attr("class", (d) => `dot ${d.cluster_id}`)
                .attr("cx", (d) => xScale(d.x ? d.x : 0))
                .attr("cy", (d) => yScale(d.y ? d.y : 0))
                .attr("r", 6)
                .style("fill", (d) => colorScale(d.expression))
                .style("stroke", "black")
                .style("stroke-width", 2)
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

            //// const legendCellType = d3.select("#drilldown-scatterplot-legend");
            //// const uniqueClusterIds = Array.from(new Set(scatterPlot.map((d) => d.cluster_id?.toString() || 'null')));
    
            //// const legendWidthCellType = 200;
            //// const clusterColorScale = d3.scaleOrdinal<string>()
            ////     .domain(uniqueClusterIds)
            ////     .range(d3.schemeCategory10);
        }

    },
        [scatterPlot])

    return (
        <Box
        sx={{height: '100%'}}
        >
            <div style={{ marginLeft: '10px', margin: '10px', padding: '10px', display: 'flex', alignItems: 'center' }}>
                <Button
                    onClick={() => { downloadJSON() }}
                    variant="outlined"
                    style={{ marginRight: '10px' }}
                >
                    JSON <FileDownloadIcon />
                </Button>
                <Button
                    variant="outlined"
                    onClick={() => { downloadCSV() }}
                    style={{ marginRight: '10px' }}
                >
                    CSV <FileDownloadIcon />
                </Button>
                <Button
                    variant="outlined"
                    onClick={() => { downloadChartAsPNG() }}
                    style={{ marginRight: '10px' }}
                >
                    <CameraAltIcon /> <FileDownloadIcon />
                </Button>
            </div>
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
