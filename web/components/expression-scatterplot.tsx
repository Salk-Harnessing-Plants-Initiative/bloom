"use client";
import * as React from 'react';
import { useEffect, useRef, useState } from "react";
import { Database } from "@/lib/database.types";
import { createClientComponentClient } from "@supabase/auth-helpers-nextjs";
import CameraAltIcon from '@mui/icons-material/CameraAlt';
import FileDownloadIcon from '@mui/icons-material/FileDownload';
import html2canvas from "html2canvas";
import Button from '@mui/material/Button';
import CircularProgress from '@mui/material/CircularProgress';
import * as d3 from "d3";

type Barcode = {
    barcode: String;
    cell_number: Number;
    cluster_id: Number;
    dataset_id: Number;
    id: Number;
    x: Number;
    y: Number;
};

export default function ExportScatterPlot({ file_id, file_name }: { file_id: number, file_name: string }) {
    const supabase = createClientComponentClient<Database>();
    const [barcode_data, setbarcodesData] = useState<Barcode[]>([]);
    const [clsuter_id, setClusterId] = useState<String[]>([]);
    const chartRef = useRef<SVGSVGElement | null>(null);
    const [loading, setLoading] = useState<boolean>(false);


    const downloadJSON = () => {
        const dataStr = JSON.stringify(barcode_data, null, 2);
        const blob = new Blob([dataStr], { type: "application/json" });
        const url = URL.createObjectURL(blob);

        const a = document.createElement("a");
        a.href = url;
        a.download = "UMAP_data.json";
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        URL.revokeObjectURL(url);
    };

    const downloadCSV = () => {
        if (barcode_data.length === 0) return;
        const headers = Object.keys(barcode_data[0]).join(",") + "\n";
        const rows = barcode_data.map(row =>
            Object.values(row).map(value => `"${value}"`).join(",")
        ).join("\n");
        const csvString = headers + rows;
        const blob = new Blob([csvString], { type: "text/csv" });
        const url = URL.createObjectURL(blob);
        const a = document.createElement("a");
        a.href = url;
        a.download = "UMAP_data.csv";
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        URL.revokeObjectURL(url);
    };

    const downloadChartAsPNG = () => {
        const divElement = document.getElementById("UMAP_plot");
        if (!divElement) return;

        html2canvas(divElement, {
            allowTaint: true,
            useCORS: true,
            logging: true,
        }).then((canvas) => {
            const link = document.createElement("a");
            link.href = canvas.toDataURL("image/png");
            link.download = "UMAP.png";
            document.body.appendChild(link);
            link.click();
            document.body.removeChild(link);
        }).catch((error) => {
            console.error("Error capturing screenshot:", error);
        });
    };

    useEffect(() => {
        const fetchData = async () => {
            try {
                setLoading(true);
                let allData: any[] = [];
                let from = 0;
                const batchSize = 1000;

                while (true) {
                    const { data, error } = await supabase
                        .from("scrna_cells")
                        .select("*")
                        .eq("dataset_id", file_id)
                        .range(from, from + batchSize - 1);

                    if (error) {
                        console.error("Error fetching data:", error.message);
                        break;
                    }
                    if (data.length === 0) break;
                    allData = [...allData, ...data];
                    from += batchSize;
                }
                setbarcodesData(allData);
            } catch (error) {
                console.error("Error in fetching data:", error);
            } finally {
                setLoading(false);
            }
        };
        if (file_id) {
            fetchData();
        }
    }, [file_id]);


    useEffect(() => {
        if (!chartRef.current) return;
        if (barcode_data && barcode_data.length > 0) {

            const margin = { top: 10, right: 30, bottom: 30, left: 60 };
            const container = chartRef.current.getBoundingClientRect();
            const width = window.innerWidth - 300 - margin.left - margin.right;
            const height = container.height - margin.top - margin.bottom;

            d3.select(chartRef.current).selectAll("*").remove();
            const xExtent = d3.extent(barcode_data, (d) => d.x) as [number, number];
            const yExtent = d3.extent(barcode_data, (d) => d.y) as [number, number];
            const xMin = Math.min(xExtent[0], 0);
            const xMax = Math.max(xExtent[1], 0);
            const yMin = Math.min(yExtent[0], 0);
            const yMax = Math.max(yExtent[1], 0);

            const xScale = d3.scaleLinear().domain([xMin, xMax]).range([20, width - 20]);
            const yScale = d3.scaleLinear().domain([yMin, yMax]).range([height - 20, 20]);
            const xAxis = d3.axisBottom(xScale);
            const yAxis = d3.axisLeft(yScale);

            const svg = d3
                .select(chartRef.current)
                .attr("width", width)
                .attr("height", height)
                .append("g")
                .attr("transform", `translate(${margin.left},${margin.top})`);

            svg.append("g")
                .attr("class", "x axis")
                .attr("transform", `translate(0,${yScale(0)})`)
                .call(xAxis)
                .selectAll("path, line, text")
                .style("stroke", "black");
            // .style("fill", "black");

            svg.append("g")
                .attr("class", "y axis")
                .attr("transform", `translate(${xScale(0)},0)`)
                .call(yAxis)
                .selectAll("path, line, text")
                .style("stroke", "black");
            // .style("fill", "black");

            svg.append("g")
                .attr("transform", `translate(0,${height})`)
                .call(d3.axisBottom(xScale));
            svg.append("g").call(d3.axisLeft(yScale));

            const uniqueLabels = Array.from(new Set(barcode_data.map((d) => d.cluster_id.toString())));
            const color = d3.scaleOrdinal().domain(uniqueLabels).range(d3.schemeCategory10);

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


            const highlight = (event: any, d: any) => {
                tooltip
                    .style("visibility", "visible")
                    .html(
                        `<strong>Barcode:</strong> ${d.barcode}<br>
                        <strong>c1:</strong> ${d.x}<br>
                        <strong>c2:</strong> ${d.y}`
                    )
                    .style("left", `${event.pageX + 10}px`)
                    .style("top", `${event.pageY + 10}px`);

                const doNotHighlight = (event: any, d: any) => {
                    tooltip.style("visibility", "hidden");

                    d3.selectAll(`.${d.cluster_id}`)
                        .transition()
                        .duration(200)
                        .style("fill", color(d.cluster_id) as string)
                        .attr("r", 5);
                };

                const selectedLabel = d.cluster_id;
                d3.selectAll(".dot")
                    .filter((dot: any) => dot.cluster_id === selectedLabel)
                    .transition()
                    .duration(200)
                    .style("fill", color(selectedLabel) as string)
                    .style("stroke", "black")
                    .style("stroke-width", 4)
                    .attr("r", 8);
                d3.selectAll(".dot")
                    .filter((dot: any) => dot.cluster_id !== selectedLabel)
                    .transition()
                    .duration(200)
                    .style("fill", "lightgrey")
                    // .style("stroke", "black")
                    // .style("stroke-width", 2)
                    .attr("r", 3);

                const darkerColor = d3.color(color(d.cluster_id) as string)?.darker(0.3)?.toString();
                d3.select(event.target)
                    .transition()
                    .duration(200)
                    .style("stroke", "red")
                    .style("stroke-width", 10)
                    .style("fill", darkerColor as string)
                    .attr("r", 8);
            };

            const doNotHighlight = (event: any, d: any) => {
                tooltip.style("visibility", "hidden");

                d3.selectAll(".dot")
                    .transition()
                    .duration(200)
                    .style("stroke", "none")
                    .style("stroke-width", 0)
                    .style("fill", (d: any) => {
                        const colorValue = color(d.cluster_id) as string;
                        return colorValue;
                    })
                    // .style("stroke", "black")
                    // .style("stroke-width", 2)
                    .attr("r", 6);
            };

            svg.selectAll("circle")
                .data(barcode_data)
                .enter()
                .append("circle")
                .attr("class", (d) => `dot ${d.cluster_id}`)
                .attr("cx", (d) => xScale(d.x))
                .attr("cy", (d) => yScale(d.y))
                .attr("r", 6)
                .style("fill", (d: any) => color(d.cluster_id) as string)
                // .style("stroke", "black")
                // .style("stroke-width", 2)
                .style("opacity", 0.2)
                // .style("fill-opacity", (d) => {
                //     const density = calculateDensity(d, barcode_data, 30);
                //     return opacityScale(density);
                // })
                .on("mouseover", highlight)
                .on("mouseleave", doNotHighlight);

            svg.append("text")
                .attr("class", "x label")
                .attr("text-anchor", "middle")
                .attr("x", width / 2)
                .attr("y", height + margin.bottom - 5)
                .style("font-size", "16px")
                .style("fill", "black")
                .text("UMAP_2(C2)");

            svg.append("text")
                .attr("class", "y label")
                .attr("text-anchor", "middle")
                .attr("transform", `rotate(-90)`)
                .attr("x", -height / 2)
                .attr("y", -margin.left + 15)
                .style("font-size", "16px")
                .style("fill", "black")
                .text("UMAP_1(C1)");

            const legendContainer = d3.select("#legend-container");
            legendContainer.html("");
            legendContainer
                .style("overflow-y", "auto")
                .style("margin", "40px")
                .style("border", "1px solid grey")
                .style("width", "fit-content");
            legendContainer.append("div")
                .style("font-size", "16px")
                .style("font-weight", "bold")
                .style("margin-bottom", "10px")
                .style("color", "black")
                .text("Cell Type:");
            legendContainer.selectAll(".legend-item")
                .data(uniqueLabels)
                .enter()
                .append("div")
                .attr("class", "legend-item")
                .style("display", "flex")
                .style("align-items", "center")
                .style("max-height", "600px")
                .style("overflow-y", "auto")
                .style("margin-top", "10px")
                .each(function (label) {
                    d3.select(this).append("div")
                        .style("width", "20px")
                        .style("height", "30px")
                        .style("background-color", color(label) as string);

                    d3.select(this).append("span")
                        .style("margin-left", "5px")
                        .style("font-size", "14px")
                        .style("color", "black")
                        .text(label);
                    
                    d3.select(this).on("click", function() {
                        // console.log("Legend item clicked:", label);
                        setClusterId((prevValues) => [...prevValues, label]);
        
                        //// d3.selectAll('.data-point')
                        ////     .style("opacity", (d) => d.label === label ? 1 : 0.2);
                    });
                });
        }

        // console.log(setClusterId);
        // console.log("*************************")
    }, [barcode_data]);

    return (
        
        <>
            {loading ? (
                <div style={{ display: 'flex', justifyContent: 'center', alignItems: 'center', height: '100vh' }}>
                <CircularProgress /> 
                </div>
            ) : (
                <div style={{ minHeight: '800px' }}>
                    <div style={{ marginLeft: '30px', margin: '10px', padding: '10px', display: 'flex', gap: '10px' }}>
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
                    {/* <svg style={{ minHeight: '800px' }} ref={chartRef}></svg><div id="legend-container"></div> */}
                    <div id="UMAP_plot" style={{ display: "flex" }}>
                        <div style={{ flex: 4, display: "flex", justifyContent: "center", alignItems: "center" }}>
                            <svg style={{ height: "800px", width: "100%" }} ref={chartRef}></svg>
                        </div>
                        <div id="legend-container" style={{ flex: 1, padding: "10px", height:"800px", overflowY: "auto", border: "1px sloid grey" }}></div>
                    </div>
                </div>)}
        </>
    )

}