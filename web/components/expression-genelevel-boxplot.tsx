"use client";
import * as React from 'react';
import { useState, useEffect, useRef } from 'react';
import { Database } from "@/lib/database.types";
import { createClientComponentClient } from "@supabase/auth-helpers-nextjs";
import * as d3 from "d3";
import Switch from '@mui/material/Switch';
import FileDownloadIcon from '@mui/icons-material/FileDownload';
import Button from '@mui/material/Button';
import html2canvas from "html2canvas";
import CameraAltIcon from '@mui/icons-material/CameraAlt';
import Box from '@mui/material/Box';


export interface StatsDataValue {
    nestedKey: string;
    q1: number;
    median: number;
    q3: number;
    min: number;
    max: number;
    lowerWhisker: number;
    upperWhisker: number;
}

export interface StatsData {
    key: string;
    counts: {
        expression: number;
        barcode: string;
    }[];
    value: StatsDataValue;
}

type GeneData = {
    gene_id: number;
    gene_name: string;
    counts: any;
    data: {
        cluster_id: number | null;
        barcode: string | null;
        cell_number: number;
        x: number | null;
        y: number | null;
    }[] | null;
}

type BoxPlotData = {
    gene_name: string,
    cluster_id: number | null;
    barcode: string | null;
    cell_number: number;
    x: number | null;
    y: number | null;
    value: number | null;
}


export default function ExpressionGeneLevelBoxPlot({ input_array, gene_name, gene_id, file_id, setScatterPlotData, handleshowplot, setDrillDownGene }: { input_array: Record<string, GeneData>, gene_name: string, gene_id: number, file_id: number, setScatterPlotData: (data: any) => void, handleshowplot: (value: boolean) => void, setDrillDownGene: (gene_name: string) => void }) {
    const supabase = createClientComponentClient<Database>();
    const chartRef = useRef<SVGSVGElement | null>(null);
    //TODO: replace type of any with appropriate type
    const [gene_data, setGeneData] = useState<{ [key: string]: any }[]>([]); // --

    const [plot_data, setPlotData] = useState<{ [gene_name: string]: BoxPlotData[] }>({});
    const [show_circle, setShowCirle] = useState(false);
    const [stats_data, setStatsData] = useState<StatsData[]>([]);

    const handleToggle = () => {
        setShowCirle(!show_circle);
    };

    const downloadJSON = () => {
        if(!stats_data) return 
        const dataStr = JSON.stringify(stats_data, null, 2);
        const blob = new Blob([dataStr], { type: "application/json" });
        const url = URL.createObjectURL(blob);

        const a = document.createElement("a");
        a.href = url;
        a.download = "gene_expression_boxplot.json";
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        URL.revokeObjectURL(url);
    }

    const downloadCSV = () => {
        if (stats_data.length === 0) return;
        const headers = Object.keys(stats_data[0]).join(",") + "\n";
        const rows = stats_data.map(row =>
            Object.values(row).map(value => JSON.stringify(value)).join(",")
        ).join("\n");
        const csvString = headers + rows;
        const blob = new Blob([csvString], { type: "text/csv" });
        const url = URL.createObjectURL(blob);
        const a = document.createElement("a");
        a.href = url;
        a.download = "gene_expression_boxplot.csv";
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        URL.revokeObjectURL(url);
    }


    const downloadChartAsPNG = () => {
        const divElement = document.getElementById("image-box-plot");
        if (!divElement) return;
    
        html2canvas(divElement, {
            allowTaint: true,
            useCORS: true,
            logging: true,
        }).then((canvas) => {
            const link = document.createElement("a");
            link.href = canvas.toDataURL("image/png");
            link.download = "boxplot_expression_level_across_clusters.png";
            document.body.appendChild(link);
            link.click();
            document.body.removeChild(link);
        }).catch((error) => {
            console.error("Error capturing screenshot:", error);
        });
    };

    useEffect(() => {
        const transformData = (input_array: Record<string, GeneData>): { [gene_name: string]: BoxPlotData[] } => {
            let result: { [key: string]: BoxPlotData[] } = {}

            Object.values(input_array).forEach((geneData) => {
                const { gene_name, counts, data } = geneData;
                counts.forEach((item: Record<number, number>) => {
                    Object.entries(item).forEach(([key, value]: [string, number]) => {
                        const clusterData = data?.find((cell) => cell.cell_number === (Number(key) - 1));
                        const barcode = clusterData?.barcode;
                        if (!result[gene_name]) {
                            result[gene_name] = []
                        }
                        result[gene_name].push({
                            gene_name: gene_name,
                            cluster_id: clusterData?.cluster_id ?? null,
                            barcode: barcode ?? null,
                            cell_number: Number(key) - 1,
                            x: clusterData?.x ?? null,
                            y: clusterData?.y ?? null,
                            value: value
                        });
                    });
                });
            });
            return result;
        };
        const plotdata = transformData(input_array)
        setPlotData(plotdata);

    }, [input_array]);

    useEffect(() => {
        if (plot_data) {

            const stats = Object.entries(plot_data).map(([key, value]) => {
                const gene_name = key
                const counts = value.map((item) => item.value ?? 0);
                const cells = value.map((item) => ({ expression: item.value ?? 0, barcode: String(item.barcode ?? 'NA') }));
                const sorted_values = counts.sort(d3.ascending);
                const q1 = d3.quantile(sorted_values, 0.25) || 0
                const median = d3.quantile(sorted_values, 0.5) || 0
                const q3 = d3.quantile(sorted_values, 0.75) || 0
                const interQuantileRange = q3 - q1;
                const minVal = d3.min(sorted_values) || 0;
                const maxVal = d3.max(sorted_values) || 0;
                const lowerWhisker = q1 - 1.5 * interQuantileRange || 0
                const upperWhisker = q3 + 1.5 * interQuantileRange || 0;

                return {
                    key: gene_name,
                    counts: cells,
                    value: {
                        nestedKey: gene_name,
                        q1: q1,
                        median: median,
                        q3: q3,
                        min: minVal,
                        max: maxVal,
                        lowerWhisker: lowerWhisker,
                        upperWhisker: upperWhisker,
                    }
                }
            })

            setStatsData(stats)

            if (!chartRef.current) return;
            d3.select(chartRef.current).selectAll("*").remove();
            const container = chartRef.current.getBoundingClientRect();
            const margin = { top: 10, right: 30, bottom: 60, left: 40 };
            const width = container.width - margin.left - margin.right;
            const height = container.height - margin.top - margin.bottom - 90;
            const tooltip = d3.select("#tooltip")

            const svg = d3.select(chartRef.current)
                .append("svg")
                .attr("width", width + margin.left + margin.right)
                .attr("height", height + margin.top + margin.bottom)
                .append("g")
                .attr("transform", `translate(${margin.left},${margin.top})`);

            const x = d3.scaleBand()
                .range([0, width])
                .domain(stats.filter((d): d is StatsData => d !== undefined).map(d => d!.key))
                .padding(0.5)

            svg.append("g")
                .attr("class", "x-axis")
                .attr("transform", `translate(0,${height})`)
                .call(d3.axisBottom(x))

            svg.selectAll(".tick text")
                .style('cursor', 'pointer')
                .on('mouseover', function (event, d) {
                    d3.select(this)
                        .transition()
                        .duration(200)
                        .style('font-weight', 'bold')
                        .style('fill', 'blue')
                        .style('text-shadow', '0 0 10px rgba(0,0,255,0.8)');
                })
                .on('mouseout', function (event, d) {
                    d3.select(this)
                        .transition()
                        .duration(200)
                        .style('font-weight', 'normal')
                        .style('fill', 'black')
                        .style('text-shadow', 'none');
                })
                .on('click', function (event, d) {
                    d3.select(this).style('fill', 'blue')
                    setDrillDownGene(String(d))
                });


            const min_values = stats.map(d => d!.value.min);
            const max_values = stats.map(d => d!.value.max);
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
                .style("font-size", "16px")
                .style("overflow", "hidden")
                .style("white-space", "nowrap")
                .style("text-overflow", "ellipsis")
                .attr("dy", "0.5em")
                .attr("transform", "rotate(-20)");

            svg.selectAll("boxPlots")
                .data(stats)
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
                        .attr("stroke", "black")
                        .attr("stroke-width", 2)
                        .on("mouseover", function () {
                            d3.select(this)
                                .style("stroke", "blue")
                                .style("stroke-width", 4)
                        })
                        .on("mouseout", function () {
                            d3.select(this)
                                .style("stroke", "black")
                                .style("stroke-width", 2)
                        })
                        .on("click", () => {
                            setDrillDownGene(d!.key);
                        });

                    let start = Math.abs(y(d!.value.q1) - y(d!.value.q3));
                    d3.select(this).append("rect")
                        .attr("x", x.bandwidth() / 2 - boxWidth / 2)
                        .attr("y", start ? y(d!.value.q3) : y(d!.value.q1) - 5)
                        // .attr("y", y(d!.value.q3))
                        .attr("width", boxWidth)
                        // .attr("height", y(d!.value.q1) - y(d!.value.q3))
                        .attr("height", Math.max(Math.abs(y(d!.value.q1) - y(d!.value.q3)), 10))
                        .attr("stroke", "black")
                        .attr("stroke-width", 2)
                        .style("fill", "#69b3a2")
                        .on("mouseover", function () {
                            d3.select(this)
                                .style("cursor", "pointer")
                                .style("fill", "blue");
                        })
                        .on("mouseout", function () {
                            d3.select(this)
                                .style("cursor", "default")
                                .style("fill", "blue");
                        })
                        .on("click", () => {
                            setDrillDownGene(d!.key);
                        });

                    d3.select(this).append("line")
                        .attr("x1", (x.bandwidth() / 2) - 25)
                        .attr("x2", (x.bandwidth() / 2) + 25)
                        .attr("y1", y(d!.value.median))
                        .attr("y2", y(d!.value.median))
                        .attr("stroke", "black")
                        .attr("stroke-width", "2")
                        .on("mouseover", function () {
                            d3.select(this)
                                .style("cursor", "pointer")
                                .style("stroke", "blue")
                                .style("stroke-width", 4)
                        })
                        .on("mouseout", function () {
                            d3.select(this)
                                .style("stroke", "black")
                                .style("stroke-width", 2)
                        })
                        .on("click", () => {
                            setDrillDownGene(d!.key);
                        });

                    d3.select(this).selectAll("circle")
                        .data(d!.counts)
                        .enter()
                        .append("circle")
                        .attr("cx", () => x.bandwidth() / 2 + (Math.random() - 0.5) * 20)
                        .attr("cy", d => y(d.expression))
                        .attr("r", 4)
                        .style("fill", (d, i, nodes) => d3.interpolateRainbow(i / nodes.length))
                        .style("opacity", 0.8)
                        .style("visibility", show_circle ? "visible" : "hidden")
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
        }
    }, [plot_data, show_circle]);

    return (
        <div style={{ height: '800px', width: '100%', marginTop: '70px' }}>
            {plot_data && (
                <div
                    style={{
                        height: '100%',
                        width: '100%',
                        display: 'flex',
                        flexDirection: 'column',
                        overflowY: 'auto',
                    }}
                >
                    <label style={{ display: 'inline-flex', alignItems: 'center', marginBottom: '10px', border: '2px solidrgb(251, 254, 255)', borderRadius: '5px' }}>
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
                                <CameraAltIcon/> <FileDownloadIcon />
                            </Button>
                            <h3 style={{ marginRight: '8px' }}>Show All Cells</h3>
                            <Switch checked={show_circle} onChange={handleToggle} />
                        </div>
                    </label>
                    <Box id="image-box-plot" style={{ height: '800px', width: '100%'}}>
                    <div
                        id='tooltip'
                        style={{
                            position: 'absolute',
                            background: 'rgba(0, 0, 0, 0.7)',
                            color: '#fff',
                            padding: '5px',
                            borderRadius: '4px',
                            pointerEvents: 'none',
                            zIndex: 10,
                        }}
                    >
                    </div>
                    <svg style={{ width: '100%', height: '100%' }} ref={chartRef}></svg>
                    </Box>
                </div>)}

        </div>
    )
}

