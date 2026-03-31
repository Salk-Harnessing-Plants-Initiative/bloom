import React, { useEffect, useState, useRef, useMemo } from "react";
import * as d3 from "d3";
import {
    Box,
    Typography,
    TextField,
    Autocomplete,
    Chip,
    Paper,
    CircularProgress,
    Alert,
    IconButton,
    Tooltip
} from "@mui/material";
import { Database } from "@/lib/database.types";
import { createClientSupabaseClient } from "@/lib/supabase/client";
import Button from '@mui/material/Button';
import DeleteIcon from '@mui/icons-material/Delete';
import RefreshIcon from '@mui/icons-material/Refresh';
import FileDownloadIcon from '@mui/icons-material/FileDownload';

type GeneData = { [key: string]: { [key: string]: number } };
type GeneList = { id: number; gene_name: string }[];

type ScrnaCount = {
    id: number;
    gene_id: number;
    dataset_id: number;
    counts_object_path: string;
};

// Pearson correlation calculation
function calculatePearsonCorrelation(x: number[], y: number[]): number {
    const n = x.length;
    if (n === 0 || n !== y.length) return 0;

    const sumX = x.reduce((a, b) => a + b, 0);
    const sumY = y.reduce((a, b) => a + b, 0);
    const sumXY = x.reduce((acc, xi, i) => acc + xi * y[i], 0);
    const sumX2 = x.reduce((a, b) => a + b * b, 0);
    const sumY2 = y.reduce((a, b) => a + b * b, 0);

    const numerator = n * sumXY - sumX * sumY;
    const denominator = Math.sqrt((n * sumX2 - sumX * sumX) * (n * sumY2 - sumY * sumY));

    if (denominator === 0) return 0;
    return numerator / denominator;
}

export default function CorrelationAnalysis({ file_id }: { file_id: number }) {
    const [selectedGenes, setSelectedGenes] = useState<string[]>([]);
    const chartRef = useRef<SVGSVGElement | null>(null);
    const [geneData, setGeneData] = useState<GeneData>({});
    const [geneNames, setGeneNames] = useState<GeneList>([]);
    const [loading, setLoading] = useState(false);
    const [initialLoading, setInitialLoading] = useState(true);
    const [searchValue, setSearchValue] = useState<{ id: number; gene_name: string } | null>(null);
    const [inputValue, setInputValue] = useState("");
    const supabase = createClientSupabaseClient();

    // Fetch gene list on mount
    useEffect(() => {
        const fetchGeneList = async () => {
            try {
                setInitialLoading(true);
                const { data: gene_list_res, error } = await supabase
                    .from("scrna_genes")
                    .select("id, gene_name")
                    .eq("dataset_id", file_id)
                    .order("gene_name");

                if (error) {
                    console.error("Error fetching gene list:", error);
                    return;
                }

                if (gene_list_res) {
                    setGeneNames(gene_list_res);
                    // Load initial sample genes (first 3 genes as example)
                    if (gene_list_res.length >= 2) {
                        const initialGenes = gene_list_res.slice(0, 3);
                        for (const gene of initialGenes) {
                            await fetchCountsData(gene.id, gene.gene_name);
                        }
                        setSelectedGenes(initialGenes.map(g => g.gene_name));
                    }
                }
            } catch (error) {
                console.error("Error in fetching data:", error);
            } finally {
                setInitialLoading(false);
            }
        };

        if (file_id) {
            fetchGeneList();
        }
    }, [file_id]);

    const fetchCountsData = async (gene_id: number, gene_name: string): Promise<boolean> => {
        try {
            const { data: geneDataRes, error: geneError } = await supabase
                .from("scrna_counts")
                .select("*")
                .eq("gene_id", gene_id)
                .eq("dataset_id", file_id) as { data: ScrnaCount[] | null; error: any };

            if (geneError || !geneDataRes?.[0]?.counts_object_path) {
                console.error("Error fetching counts:", geneError);
                return false;
            }

            const { data: storageData } = await supabase.storage
                .from("scrna")
                .download(geneDataRes[0].counts_object_path);

            if (storageData) {
                const fileText = await storageData.text();
                const jsonData = JSON.parse(fileText);
                const formattedGeneData: { [key: string]: number } = {};
                for (const [cellId, value] of Object.entries(jsonData)) {
                    formattedGeneData[cellId] = value as number;
                }

                setGeneData((prevVal) => ({ ...prevVal, [gene_name]: formattedGeneData }));
                return true;
            }

            return false;
        } catch (error) {
            console.error("Error in fetching counts data:", error);
            return false;
        }
    };

    const handleAddGene = async (gene: { id: number; gene_name: string } | null) => {
        if (!gene || selectedGenes.includes(gene.gene_name)) return;

        setLoading(true);
        try {
            const success = await fetchCountsData(gene.id, gene.gene_name);
            if (success) {
                setSelectedGenes((prev) => [...prev, gene.gene_name]);
            }
        } finally {
            setLoading(false);
            setSearchValue(null);
            setInputValue("");
        }
    };

    const handleRemoveGene = (geneName: string) => {
        setSelectedGenes((prev) => prev.filter((g) => g !== geneName));
        setGeneData((prev) => {
            const newData = { ...prev };
            delete newData[geneName];
            return newData;
        });
    };

    const handleClearAll = () => {
        setSelectedGenes([]);
        setGeneData({});
    };

    // Filter genes for autocomplete (exclude already selected)
    const filteredGeneOptions = useMemo(() => {
        return geneNames.filter((g) => !selectedGenes.includes(g.gene_name));
    }, [geneNames, selectedGenes]);

    // Download correlation data as CSV
    const downloadCorrelationCSV = () => {
        if (selectedGenes.length < 2) return;

        let csvContent = "Gene1,Gene2,Correlation,NumCells\n";

        for (let i = 0; i < selectedGenes.length; i++) {
            for (let j = i + 1; j < selectedGenes.length; j++) {
                const geneX = selectedGenes[i];
                const geneY = selectedGenes[j];

                const barcodesX = Object.keys(geneData[geneX] || {});
                const barcodesY = Object.keys(geneData[geneY] || {});
                const commonBarcodes = barcodesX.filter((b) => barcodesY.includes(b));

                const xValues = commonBarcodes.map((b) => geneData[geneX][b] || 0);
                const yValues = commonBarcodes.map((b) => geneData[geneY][b] || 0);

                const correlation = calculatePearsonCorrelation(xValues, yValues);
                csvContent += `${geneX},${geneY},${correlation.toFixed(4)},${commonBarcodes.length}\n`;
            }
        }

        const blob = new Blob([csvContent], { type: "text/csv" });
        const url = URL.createObjectURL(blob);
        const a = document.createElement("a");
        a.href = url;
        a.download = "gene_correlations.csv";
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        URL.revokeObjectURL(url);
    };

    const drawScatterPlot = (genes: string[]) => {
        if (!chartRef.current) return;
        d3.select(chartRef.current).selectAll("*").remove();

        const numGenes = genes.length;
        if (numGenes < 2) return;

        const container = chartRef.current.getBoundingClientRect();
        const margin = { top: 40, right: 40, bottom: 40, left: 40 };
        const totalWidth = Math.max(container.width - margin.left - margin.right, 600);
        const numPairs = (numGenes * (numGenes - 1)) / 2;
        const cols = Math.min(numPairs, 3);
        const rows = Math.ceil(numPairs / cols);
        const cellSize = Math.min(280, (totalWidth - 40 * cols) / cols);
        const totalHeight = rows * (cellSize + 80) + margin.top + margin.bottom;

        const svg = d3
            .select(chartRef.current)
            .attr("width", totalWidth + margin.left + margin.right)
            .attr("height", totalHeight);

        const tooltip = d3.select("#correlation-tooltip");

        let pairIndex = 0;
        for (let i = 0; i < numGenes; i++) {
            for (let j = i + 1; j < numGenes; j++) {
                const geneX = genes[i];
                const geneY = genes[j];

                const col = pairIndex % cols;
                const row = Math.floor(pairIndex / cols);
                const xOffset = margin.left + col * (cellSize + 40);
                const yOffset = margin.top + row * (cellSize + 80);

                const barcodesX = Object.keys(geneData[geneX] || {});
                const barcodesY = Object.keys(geneData[geneY] || {});
                const commonBarcodes = barcodesX.filter((b) => barcodesY.includes(b));

                const xValues = commonBarcodes.map((b) => geneData[geneX][b] || 0);
                const yValues = commonBarcodes.map((b) => geneData[geneY][b] || 0);

                const xMax = d3.max(xValues) || 1;
                const yMax = d3.max(yValues) || 1;

                const x = d3.scaleLinear().domain([0, xMax]).range([0, cellSize]);
                const y = d3.scaleLinear().domain([0, yMax]).range([cellSize, 0]);

                const cell = svg
                    .append("g")
                    .attr("transform", `translate(${xOffset}, ${yOffset})`);

                // Background
                cell.append("rect")
                    .attr("width", cellSize)
                    .attr("height", cellSize)
                    .attr("fill", "#fafafa")
                    .attr("stroke", "#e0e0e0")
                    .attr("stroke-width", 1)
                    .attr("rx", 4);

                // X axis
                cell.append("g")
                    .attr("transform", `translate(0,${cellSize})`)
                    .call(d3.axisBottom(x).ticks(4).tickSize(-cellSize))
                    .selectAll("line")
                    .attr("stroke", "#e0e0e0");

                // Y axis
                cell.append("g")
                    .call(d3.axisLeft(y).ticks(4).tickSize(-cellSize))
                    .selectAll("line")
                    .attr("stroke", "#e0e0e0");

                // X label
                cell.append("text")
                    .attr("x", cellSize / 2)
                    .attr("y", cellSize + 35)
                    .attr("text-anchor", "middle")
                    .attr("font-size", "12px")
                    .attr("font-weight", "bold")
                    .attr("fill", "#333")
                    .text(geneX);

                // Y label
                cell.append("text")
                    .attr("transform", "rotate(-90)")
                    .attr("x", -cellSize / 2)
                    .attr("y", -30)
                    .attr("text-anchor", "middle")
                    .attr("font-size", "12px")
                    .attr("font-weight", "bold")
                    .attr("fill", "#333")
                    .text(geneY);

                // Points
                cell.selectAll("circle")
                    .data(commonBarcodes)
                    .enter()
                    .append("circle")
                    .attr("cx", (b) => x(geneData[geneX][b] || 0))
                    .attr("cy", (b) => y(geneData[geneY][b] || 0))
                    .attr("r", 3)
                    .attr("fill", "#1976d2")
                    .attr("opacity", 0.6)
                    .on("mouseover", function (event, barcode) {
                        const valX = geneData[geneX][barcode] || 0;
                        const valY = geneData[geneY][barcode] || 0;
                        d3.select(this).attr("r", 6).attr("fill", "#f50057");
                        tooltip
                            .style("visibility", "visible")
                            .html(
                                `<strong>Cell:</strong> ${barcode}<br/>
                                 <strong>${geneX}:</strong> ${valX.toFixed(2)}<br/>
                                 <strong>${geneY}:</strong> ${valY.toFixed(2)}`
                            )
                            .style("top", event.pageY - 10 + "px")
                            .style("left", event.pageX + 10 + "px");
                    })
                    .on("mouseout", function () {
                        d3.select(this).attr("r", 3).attr("fill", "#1976d2");
                        tooltip.style("visibility", "hidden");
                    });

                // Correlation coefficient
                const correlation = calculatePearsonCorrelation(xValues, yValues);
                const correlationColor =
                    correlation > 0.5 ? "#2e7d32" : correlation < -0.5 ? "#c62828" : "#666";

                cell.append("text")
                    .attr("x", cellSize - 5)
                    .attr("y", 15)
                    .attr("text-anchor", "end")
                    .attr("font-size", "11px")
                    .attr("font-weight", "bold")
                    .attr("fill", correlationColor)
                    .text(`r = ${correlation.toFixed(3)}`);

                // Cell count
                cell.append("text")
                    .attr("x", cellSize - 5)
                    .attr("y", 28)
                    .attr("text-anchor", "end")
                    .attr("font-size", "10px")
                    .attr("fill", "#999")
                    .text(`n = ${commonBarcodes.length}`);

                pairIndex++;
            }
        }
    };

    useEffect(() => {
        if (selectedGenes.length >= 2 && Object.keys(geneData).length >= 2) {
            drawScatterPlot(selectedGenes);
        } else if (chartRef.current) {
            d3.select(chartRef.current).selectAll("*").remove();
        }
    }, [selectedGenes, geneData]);

    if (initialLoading) {
        return (
            <Box display="flex" justifyContent="center" alignItems="center" minHeight="400px">
                <CircularProgress />
                <Typography sx={{ ml: 2 }}>Loading gene data...</Typography>
            </Box>
        );
    }

    return (
        <Box sx={{ p: 2 }}>
            {/* Header */}
            <Typography variant="h5" sx={{ mb: 2, fontWeight: "bold" }}>
                Gene Co-Expression Analysis
            </Typography>
            <Typography variant="body2" color="text.secondary" sx={{ mb: 3 }}>
                Search and select genes to visualize their expression correlation. Each scatter plot
                shows the expression levels of two genes across all cells, with the Pearson
                correlation coefficient (r) displayed.
            </Typography>

            {/* Gene Search */}
            <Paper sx={{ p: 2, mb: 3 }}>
                <Typography variant="subtitle2" sx={{ mb: 1, fontWeight: "bold" }}>
                    Add Genes to Compare
                </Typography>
                <Box display="flex" alignItems="center" gap={2}>
                    <Autocomplete
                        value={searchValue}
                        onChange={(_, newValue) => handleAddGene(newValue)}
                        inputValue={inputValue}
                        onInputChange={(_, newInputValue) => setInputValue(newInputValue)}
                        options={filteredGeneOptions}
                        getOptionLabel={(option) => option.gene_name}
                        loading={loading}
                        sx={{ width: 300 }}
                        renderInput={(params) => (
                            <TextField
                                {...params}
                                label="Search genes..."
                                variant="outlined"
                                size="small"
                                InputProps={{
                                    ...params.InputProps,
                                    endAdornment: (
                                        <>
                                            {loading ? <CircularProgress size={20} /> : null}
                                            {params.InputProps.endAdornment}
                                        </>
                                    ),
                                }}
                            />
                        )}
                    />
                    <Typography variant="body2" color="text.secondary">
                        {geneNames.length.toLocaleString()} genes available
                    </Typography>
                </Box>
            </Paper>

            {/* Selected Genes */}
            <Paper sx={{ p: 2, mb: 3 }}>
                <Box display="flex" justifyContent="space-between" alignItems="center" mb={1}>
                    <Typography variant="subtitle2" sx={{ fontWeight: "bold" }}>
                        Selected Genes ({selectedGenes.length})
                    </Typography>
                    <Box>
                        <Tooltip title="Download correlation data">
                            <IconButton
                                size="small"
                                onClick={downloadCorrelationCSV}
                                disabled={selectedGenes.length < 2}
                            >
                                <FileDownloadIcon />
                            </IconButton>
                        </Tooltip>
                        <Tooltip title="Clear all genes">
                            <IconButton
                                size="small"
                                onClick={handleClearAll}
                                disabled={selectedGenes.length === 0}
                            >
                                <DeleteIcon />
                            </IconButton>
                        </Tooltip>
                    </Box>
                </Box>
                <Box display="flex" flexWrap="wrap" gap={1}>
                    {selectedGenes.length === 0 ? (
                        <Typography variant="body2" color="text.secondary">
                            No genes selected. Use the search box above to add genes.
                        </Typography>
                    ) : (
                        selectedGenes.map((gene) => (
                            <Chip
                                key={gene}
                                label={gene}
                                onDelete={() => handleRemoveGene(gene)}
                                color="primary"
                                variant="outlined"
                            />
                        ))
                    )}
                </Box>
            </Paper>

            {/* Info Alert */}
            {selectedGenes.length === 1 && (
                <Alert severity="info" sx={{ mb: 2 }}>
                    Add at least one more gene to see correlation plots.
                </Alert>
            )}

            {/* Scatter Plot Area */}
            <Paper sx={{ p: 2, minHeight: 400, overflow: "auto" }}>
                <div
                    id="correlation-tooltip"
                    style={{
                        position: "absolute",
                        visibility: "hidden",
                        backgroundColor: "white",
                        border: "1px solid #ccc",
                        borderRadius: "4px",
                        padding: "8px 12px",
                        boxShadow: "0 2px 8px rgba(0,0,0,0.15)",
                        fontSize: "12px",
                        zIndex: 1000,
                        pointerEvents: "none",
                    }}
                />
                {selectedGenes.length < 2 ? (
                    <Box
                        display="flex"
                        flexDirection="column"
                        justifyContent="center"
                        alignItems="center"
                        minHeight={300}
                    >
                        <Typography variant="h6" color="text.secondary">
                            Select at least 2 genes to view correlation plots
                        </Typography>
                        <Typography variant="body2" color="text.secondary" sx={{ mt: 1 }}>
                            Use the search box above to find and add genes
                        </Typography>
                    </Box>
                ) : (
                    <svg ref={chartRef} style={{ width: "100%", minHeight: 400 }} />
                )}
            </Paper>
        </Box>
    );
}
