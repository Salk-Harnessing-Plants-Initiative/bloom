import React, { useEffect, useRef, useState } from "react";
import Typography from '@mui/material/Typography';
import Box from '@mui/material/Box';
import Button from '@mui/material/Button';
import html2canvas from "html2canvas";
import CameraAltIcon from '@mui/icons-material/CameraAlt';
import FileDownloadIcon from '@mui/icons-material/FileDownload';
import * as d3 from "d3";


type PlotStats = {
    key: string;
    expression: number[];
    barcodes: string[];
    points: { expression: number; barcode: string }[];
    value: {
        q1: number;
        median: number;
        q3: number;
        min: number;
        max: number;
        lowerWhisker: number;
        upperWhisker: number;
    };
};

export default function GeneDrillDownBoxPots({ BoxPlotData, geneName }: { BoxPlotData: PlotStats[], geneName: string }) {
    const chartRef = useRef<SVGSVGElement | null>(null);

    const downloadJSON = () => {
        const dataStr = JSON.stringify(BoxPlotData, null, 2);
        const blob = new Blob([dataStr], { type: "application/json" });
        const url = URL.createObjectURL(blob);

        const a = document.createElement("a");
        a.href = url;
        a.download = `${geneName}_expression_across_celltypes.json`;
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        URL.revokeObjectURL(url);
    }

    const downloadCSV = () => {
        if (BoxPlotData.length === 0) return;
        const headers = Object.keys(BoxPlotData[0]).join(",") + "\n";
        const rows = BoxPlotData.map(row =>
            Object.values(row).map(value => `"${value}"`).join(",")
        ).join("\n");
        const csvString = headers + rows;
        const blob = new Blob([csvString], { type: "text/csv" });
        const url = URL.createObjectURL(blob);
        const a = document.createElement("a");
        a.href = url;
        a.download = "${geneName}_expression_across_celltypes.csv";
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        URL.revokeObjectURL(url);
    }

    const downloadChartAsPNG = () => {
        const divElement = document.getElementById("image-drilldown-boxplot");
        if (!divElement) return;
    
        html2canvas(divElement, {
            allowTaint: true,
            useCORS: true,
            logging: true,
        }).then((canvas) => {
            const link = document.createElement("a");
            link.href = canvas.toDataURL("image/png");
            link.download = `${geneName}_expression_across_celltypes.png`;
            document.body.appendChild(link);
            link.click();
            document.body.removeChild(link);
        }).catch((error) => {
            console.error("Error capturing screenshot:", error);
        });
    };

    useEffect(() => {
        if (!chartRef.current || !BoxPlotData) return;

        d3.select(chartRef.current).selectAll("*").remove();
        const container = chartRef.current.getBoundingClientRect();
        const margin = { top: 10, right: 30, bottom: 70, left: 40 };
        const width = container.width - margin.left - margin.right;
        const height = container.height - margin.top - margin.bottom - 90;
        const tooltip = d3.select("#tooltip-tab")

        const svg = d3.select(chartRef.current)
            .append("svg")
            .attr("width", width + margin.left + margin.right)
            .attr("height", height + margin.top + margin.bottom)
            .append("g")
            .attr("transform", `translate(${margin.left},${margin.top})`);

        const x = d3.scaleBand()
            .range([0, width])
            .domain(BoxPlotData.filter((d): d is PlotStats => d !== undefined).map(d => d!.key))
            .padding(0.5);

        svg.append("g")
            .attr("class", "x-axis")
            .attr("transform", `translate(0,${height})`)
            .call(d3.axisBottom(x));

        const min_values = BoxPlotData.map(d => d!.value.min);
        const max_values = BoxPlotData.map(d => d!.value.max);

        const minValue = Number(d3.min(min_values));
        const maxValue = Number(d3.max(max_values));

        const y = d3.scaleLinear()
            .domain([minValue - 1.0, maxValue + 1.0])
            .range([height, 0]);

        const boxWidth = 50;
        svg.append("g")
            .call(d3.axisLeft(y));

        svg.append("text")
            .attr("transform", "rotate(-90)")
            .attr("y", margin.left - 80)
            .attr("x", -height / 2)
            .attr("dy", "1em")
            .style("text-anchor", "middle")
            .style("font-size", "14px")
            .text("Counts");

        svg.selectAll(".x-axis text")
            .style("text-anchor", "middle")
            .style("font-size", "20px")
            .style("overflow", "hidden")
            .style("white-space", "nowrap")
            .style("text-overflow", "ellipsis")
            .attr("dy", "0.5em")
            .attr("transform", "rotate(-20)");

        svg.selectAll("boxPlots")
            .data(BoxPlotData)
            .enter()
            .append("g")
            .attr("transform", d => `translate(${x(d!.key)}, 0)`)

            .each(function (d) {
                let center = Number(x(d!.key)) | 0;
                d3.select(this).append("line")
                    .attr("x1", x.bandwidth() / 2)
                    .attr("x2", x.bandwidth() / 2)
                    .attr("y1", y(d!.value.min))
                    .attr("y2", y(d!.value.max))
                    .attr("stroke", "black");

                let start = Math.abs(y(d!.value.q1) - y(d!.value.q3));
                d3.select(this).append("rect")
                    .attr("x", x.bandwidth() / 2 - boxWidth / 2)
                    .attr("y", start ? y(d!.value.q3) : y(d!.value.q1) - 5)
                    .attr("width", boxWidth)
                    .attr("height", Math.max(Math.abs(y(d!.value.q1) - y(d!.value.q3)), 10))
                    .attr("stroke", "black")
                    .style("fill", "#69b3a2");

                d3.select(this).append("line")
                    .attr("x1", (x.bandwidth() / 2) - 25)
                    .attr("x2", (x.bandwidth() / 2) + 25)
                    .attr("y1", y(d!.value.median))
                    .attr("y2", y(d!.value.median))
                    .attr("stroke", "black");

                d3.select(this).selectAll("circle")
                    .data(d!.points)
                    .enter()
                    .append("circle")
                    .attr("cx", () => x.bandwidth() / 2 + (Math.random() - 0.5) * 20)
                    .attr("cy", d => y(d.expression))
                    .attr("r", 3)
                    .style("fill", (d, i, nodes) => d3.interpolateRainbow(i / nodes.length))
                    .style("opacity", 0.8)
                    .style("visibility", "visible")
                    .on("mouseover", function (event, d) {
                        tooltip.style("visibility", "visible")
                            .html(`Expression Value: ${d.expression}<br>Barcode: ${d.barcode}`)
                            .style("left", `${event.pageX + 10}px`)
                            .style("top", `${event.pageY + 10}px`);
                    })
                    .on("mouseout", function () {
                        tooltip.style("visibility", "hidden");
                    });

            });

    }, [BoxPlotData])

    return (
        <>
            <div
                id='tooltip-tab'
                style={{
                    position: 'absolute',
                    background: 'rgba(0, 0, 0, 0.7)',
                    color: '#fff',
                    padding: '5px',
                    borderRadius: '4px',
                    pointerEvents: 'none',
                    zIndex: 10,
                    visibility: 'hidden',
                }}
            >
            </div>
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
            <Box id='image-drilldown-boxplot' sx={{width:'100%', height:'700px'}}>
            <Typography variant="h6" align="center">
                {geneName} Expression Levels Across Cell Types
            </Typography>
            <svg style={{ width: '100%', height: '100%' }} ref={chartRef}></svg>
            </Box>
        </>

    )
}