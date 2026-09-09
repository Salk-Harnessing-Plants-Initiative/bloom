import * as React from 'react';
import { useState, useEffect, useMemo, useRef } from 'react';
import { DataGrid, GridColDef } from '@mui/x-data-grid';
import Paper from '@mui/material/Paper';
import Button from '@mui/material/Button';
import { Database } from "@/lib/database.types";
import { createClientSupabaseClient } from "@/lib/supabase/client";
import Box from '@mui/material/Box';
import InputLabel from '@mui/material/InputLabel';
import MenuItem from '@mui/material/MenuItem';
import FormControl from '@mui/material/FormControl';
import Select from '@mui/material/Select';
import Typography from '@mui/material/Typography';
import Alert from '@mui/material/Alert';
import CircularProgress from '@mui/material/CircularProgress';
import Chip from '@mui/material/Chip';
import Tooltip from '@mui/material/Tooltip';
import IconButton from '@mui/material/IconButton';
import FileDownloadIcon from '@mui/icons-material/FileDownload';
import InfoOutlinedIcon from '@mui/icons-material/InfoOutlined';
import * as d3 from "d3";

type GeneData = {
  gene: string;
  p_val: number;
  avg_log2FC: number;
  'pct.1': number;
  'pct.2': number;
  p_val_adj: number;
  _row: string;
};

const columns: GridColDef[] = [
  { field: '_row', headerName: 'Gene Name', width: 180 },
  {
    field: 'avg_log2FC',
    headerName: 'Log2 Fold Change',
    width: 140,
    renderCell: (params) => {
      const value = params.value as number;
      const color = value > 0 ? '#2e7d32' : value < 0 ? '#c62828' : '#666';
      return <span style={{ color, fontWeight: 'bold' }}>{value?.toFixed(3)}</span>;
    }
  },
  {
    field: 'p_val_adj',
    headerName: 'Adj. p-value',
    width: 120,
    renderCell: (params) => {
      const value = params.value as number;
      return <span>{value?.toExponential(2)}</span>;
    }
  },
  {
    field: 'pct.1',
    headerName: '% in Cluster',
    width: 120,
    renderCell: (params) => {
      const value = params.value as number;
      return <span>{(value * 100).toFixed(1)}%</span>;
    }
  },
  {
    field: 'pct.2',
    headerName: '% in Others',
    width: 120,
    renderCell: (params) => {
      const value = params.value as number;
      return <span>{(value * 100).toFixed(1)}%</span>;
    }
  },
  {
    field: 'p_val',
    headerName: 'Raw p-value',
    width: 120,
    renderCell: (params) => {
      const value = params.value as number;
      return <span>{value?.toExponential(2)}</span>;
    }
  }
];

export function DataTable({ rows }: { rows: GeneData[] }) {
  return (
    <Paper sx={{ height: 500, width: '100%' }}>
      <DataGrid
        rows={rows ?? []}
        columns={columns}
        getRowId={(row) => row._row}
        initialState={{
          pagination: {
            paginationModel: { pageSize: 25, page: 0 },
          },
          sorting: {
            sortModel: [{ field: 'p_val_adj', sort: 'asc' }],
          },
        }}
        pageSizeOptions={[10, 25, 50, 100]}
        checkboxSelection
        sx={{ border: 0 }}
      />
    </Paper>
  );
}

/** A row of scrna_de.
 *
 * A null `file_path` means the comparison was considered and never run — the
 * group sizes on the row are what explain why, so it is shown rather than
 * hidden. A null `contrast` is an older one-vs-rest row, which has one
 * selector and no groups to name.
 */
type DeEntry = {
  cluster_id: string | null;
  file_path: string | null;
  contrast: string | null;
  group1: string | null;
  group2: string | null;
  n_group1: number | null;
  n_group2: number | null;
  n_genes_tested: number | null;
  n_significant_fdr_lfc: number | null;
};

/** What a comparison is called in the selectors. */
function contrastLabel(entry: DeEntry): string {
  return entry.contrast ?? "vs all other cells";
}

/** "142 of 15,430 significant", straight from the row — no file needed. */
export function significanceLabel(entry: DeEntry): string {
  if (entry.n_genes_tested === null || entry.n_significant_fdr_lfc === null) {
    return "";
  }
  if (entry.n_genes_tested === 0) return "not tested";
  const fmt = new Intl.NumberFormat("en-US");
  return `${fmt.format(entry.n_significant_fdr_lfc)} of ` +
    `${fmt.format(entry.n_genes_tested)} significant`;
}

/** Which way round the fold change reads, in the names of the two groups. */
export function directionLabel(entry: DeEntry): string {
  if (!entry.group1 || !entry.group2) {
    return "A positive fold change is higher in this cell type than in the rest.";
  }
  return `A positive fold change is higher in ${entry.group1} than in ` +
    `${entry.group2}; a negative one is higher in ${entry.group2}.`;
}

export default function DifferentialExpressionAnalysis({ file_id }: { file_id: number }) {
  const [clusterList, setClusterList] = useState<DeEntry[]>([]);
  const [selectedCluster, setSelectedCluster] = useState<DeEntry | null>(null);
  const [chartData, setChartData] = useState<GeneData[] | null>(null);
  const [loading, setLoading] = useState(true);
  const [dataLoading, setDataLoading] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);
  const supabase = createClientSupabaseClient();
  const chartRef = useRef<SVGSVGElement | null>(null);

  // Fetch cluster list
  useEffect(() => {
    const fetchData = async () => {
      setLoading(true);
      const { data: clusterlabels, error } = await supabase
        .from("scrna_de")
        // One literal, because the typed client reads the column list from
        // the string itself to work out the row shape.
        .select("cluster_id, file_path, contrast, group1, group2, n_group1, n_group2, n_genes_tested, n_significant_fdr_lfc")
        .eq("dataset_id", file_id);

      if (error) {
        console.error("Error fetching DE clusters:", error);
      }

      setClusterList(clusterlabels || []);
      if (clusterlabels && clusterlabels.length > 0) {
        setSelectedCluster(clusterlabels[0]);
      }
      setLoading(false);
    };
    fetchData();
  }, [file_id]);

  // Fetch DE data for selected cluster
  useEffect(() => {
    if (!selectedCluster) return;

    setChartData(null);
    const filePath = selectedCluster.file_path;
    if (!filePath) {
      setDataLoading(false);
      setLoadError(null);
      return;
    }

    let cancelled = false;
    const fetchData = async () => {
      setDataLoading(true);
      setLoadError(null);
      const { data: storageData, error: storageError } = await supabase.storage
        .from("scrna")
        .download(filePath);

      if (cancelled) return;
      if (storageError || !storageData) {
        setChartData(null);
        setLoadError(storageError?.message ?? "the file could not be read");
        setDataLoading(false);
        return;
      }
      const textData = await storageData.text();
      if (cancelled) return;
      try {
        setChartData(JSON.parse(textData));
      } catch {
        setChartData(null);
        setLoadError("the file could not be read");
      }
      setDataLoading(false);
    };
    fetchData();
    // Switching comparison while one is in flight would otherwise let the
    // slower answer land last and draw itself under the new comparison's name.
    return () => {
      cancelled = true;
    };
  }, [selectedCluster]);

  // The cell types, in the order the rows came back, each appearing once.
  const cellTypes: string[] = useMemo(() => {
    const seen: string[] = [];
    for (const row of clusterList) {
      const name = row.cluster_id ?? "";
      if (name && !seen.includes(name)) seen.push(name);
    }
    return seen;
  }, [clusterList]);

  const contrastsForCellType = useMemo(
    () => clusterList.filter((row) => row.cluster_id === selectedCluster?.cluster_id),
    [clusterList, selectedCluster],
  );

  // Draw volcano plot
  useEffect(() => {
    if (!chartData || !chartRef.current) return;

    const svg = d3.select(chartRef.current);
    svg.selectAll("*").remove();

    const width = chartRef.current.clientWidth || 600;
    const height = 500;
    const margin = { top: 40, right: 40, bottom: 60, left: 70 };
    const innerWidth = width - margin.left - margin.right;
    const innerHeight = height - margin.top - margin.bottom;

    const transformedData = chartData
      .filter((d: GeneData) => d.p_val_adj > 0 && !isNaN(d.avg_log2FC))
      .map((d: GeneData) => ({
        ...d,
        x: +d.avg_log2FC,
        y: -Math.log10(+d.p_val_adj),
      }));

    if (transformedData.length === 0) return;

    // Find significant genes for labeling
    const significantGenes = transformedData
      .filter(d => d.p_val_adj < 0.05 && Math.abs(d.x) > 1)
      .sort((a, b) => a.p_val_adj - b.p_val_adj)
      .slice(0, 10);

    const xExtent = d3.extent(transformedData, d => d.x) as [number, number];
    const yMax = d3.max(transformedData, d => d.y) || 10;

    // Symmetric x-axis
    const xMax = Math.max(Math.abs(xExtent[0]), Math.abs(xExtent[1])) * 1.1;

    const xScale = d3.scaleLinear()
      .domain([-xMax, xMax])
      .range([0, innerWidth]);

    const yScale = d3.scaleLinear()
      .domain([0, yMax * 1.1])
      .range([innerHeight, 0]);

    // Create tooltip
    const tooltip = d3.select("body").append("div")
      .attr("class", "de-tooltip")
      .style("position", "absolute")
      .style("padding", "10px")
      .style("background", "white")
      .style("border", "1px solid #ccc")
      .style("border-radius", "6px")
      .style("pointer-events", "none")
      .style("font-size", "12px")
      .style("box-shadow", "0 2px 8px rgba(0,0,0,0.15)")
      .style("display", "none")
      .style("z-index", "1000");

    const plot = svg
      .attr("width", width)
      .attr("height", height)
      .append("g")
      .attr("transform", `translate(${margin.left},${margin.top})`);

    // Background
    plot.append("rect")
      .attr("width", innerWidth)
      .attr("height", innerHeight)
      .attr("fill", "#fafafa");

    // Grid lines
    plot.append("g")
      .attr("class", "grid")
      .attr("transform", `translate(0,${innerHeight})`)
      .call(d3.axisBottom(xScale).ticks(10).tickSize(-innerHeight))
      .selectAll("line")
      .attr("stroke", "#e0e0e0");

    plot.append("g")
      .attr("class", "grid")
      .call(d3.axisLeft(yScale).ticks(10).tickSize(-innerWidth))
      .selectAll("line")
      .attr("stroke", "#e0e0e0");

    // Significance threshold lines
    // Horizontal line at -log10(0.05) ≈ 1.3
    const sigY = -Math.log10(0.05);
    plot.append("line")
      .attr("x1", 0)
      .attr("x2", innerWidth)
      .attr("y1", yScale(sigY))
      .attr("y2", yScale(sigY))
      .attr("stroke", "#999")
      .attr("stroke-dasharray", "5,5")
      .attr("stroke-width", 1);

    // Vertical lines at log2FC = ±1
    plot.append("line")
      .attr("x1", xScale(-1))
      .attr("x2", xScale(-1))
      .attr("y1", 0)
      .attr("y2", innerHeight)
      .attr("stroke", "#999")
      .attr("stroke-dasharray", "5,5")
      .attr("stroke-width", 1);

    plot.append("line")
      .attr("x1", xScale(1))
      .attr("x2", xScale(1))
      .attr("y1", 0)
      .attr("y2", innerHeight)
      .attr("stroke", "#999")
      .attr("stroke-dasharray", "5,5")
      .attr("stroke-width", 1);

    // X-axis
    plot.append("g")
      .attr("transform", `translate(0,${innerHeight})`)
      .call(d3.axisBottom(xScale))
      .selectAll("text")
      .style("font-size", "12px");

    // Y-axis
    plot.append("g")
      .call(d3.axisLeft(yScale))
      .selectAll("text")
      .style("font-size", "12px");

    // Axis labels
    plot.append("text")
      .attr("x", innerWidth / 2)
      .attr("y", innerHeight + 45)
      .attr("text-anchor", "middle")
      .attr("font-size", "14px")
      .attr("font-weight", "bold")
      .text("Log2 Fold Change");

    plot.append("text")
      .attr("transform", "rotate(-90)")
      .attr("x", -innerHeight / 2)
      .attr("y", -50)
      .attr("text-anchor", "middle")
      .attr("font-size", "14px")
      .attr("font-weight", "bold")
      .text("-Log10(Adjusted p-value)");

    // Title
    plot.append("text")
      .attr("x", innerWidth / 2)
      .attr("y", -15)
      .attr("text-anchor", "middle")
      .attr("font-size", "16px")
      .attr("font-weight", "bold")
      .text(`Volcano Plot: ${selectedCluster?.cluster_id || ''} vs Other Clusters`);

    // Points
    plot.selectAll("circle")
      .data(transformedData)
      .enter()
      .append("circle")
      .attr("cx", d => xScale(d.x))
      .attr("cy", d => yScale(d.y))
      .attr("r", d => significantGenes.some(g => g.gene === d.gene) ? 5 : 3)
      .attr("fill", d => {
        if (d.p_val_adj < 0.05 && d.x > 1) return "#c62828";  // Upregulated
        if (d.p_val_adj < 0.05 && d.x < -1) return "#1565c0"; // Downregulated
        return "#9e9e9e"; // Not significant
      })
      .attr("opacity", 0.7)
      .attr("stroke", d => significantGenes.some(g => g.gene === d.gene) ? "#000" : "none")
      .attr("stroke-width", 1)
      .on("mouseover", function (event, d) {
        d3.select(this)
          .attr("r", 7)
          .attr("opacity", 1);
        tooltip
          .style("display", "block")
          .html(`
            <strong>${d.gene}</strong><br/>
            Log2 FC: <span style="color:${d.x > 0 ? '#c62828' : '#1565c0'}">${d.x.toFixed(3)}</span><br/>
            Adj. p-value: ${d.p_val_adj.toExponential(2)}<br/>
            % in cluster: ${(d['pct.1'] * 100).toFixed(1)}%<br/>
            % in others: ${(d['pct.2'] * 100).toFixed(1)}%
          `);
      })
      .on("mousemove", (event) => {
        tooltip
          .style("left", (event.pageX + 15) + "px")
          .style("top", (event.pageY - 10) + "px");
      })
      .on("mouseout", function (event, d) {
        d3.select(this)
          .attr("r", significantGenes.some(g => g.gene === d.gene) ? 5 : 3)
          .attr("opacity", 0.7);
        tooltip.style("display", "none");
      });

    // Labels for top significant genes
    significantGenes.forEach(d => {
      plot.append("text")
        .attr("x", xScale(d.x) + 8)
        .attr("y", yScale(d.y) + 4)
        .attr("font-size", "10px")
        .attr("fill", "#333")
        .text(d.gene.length > 12 ? d.gene.substring(0, 12) + '...' : d.gene);
    });

    // Legend
    const legend = plot.append("g")
      .attr("transform", `translate(${innerWidth - 150}, 10)`);

    legend.append("rect")
      .attr("width", 140)
      .attr("height", 80)
      .attr("fill", "white")
      .attr("stroke", "#ddd")
      .attr("rx", 4);

    const legendData = [
      { color: "#c62828", label: "Upregulated" },
      { color: "#1565c0", label: "Downregulated" },
      { color: "#9e9e9e", label: "Not significant" }
    ];

    legendData.forEach((item, i) => {
      legend.append("circle")
        .attr("cx", 15)
        .attr("cy", 20 + i * 22)
        .attr("r", 5)
        .attr("fill", item.color);

      legend.append("text")
        .attr("x", 28)
        .attr("y", 24 + i * 22)
        .attr("font-size", "11px")
        .text(item.label);
    });

    return () => {
      tooltip.remove();
    };
  }, [chartData, selectedCluster]);

  // Download CSV function
  /** Download the stored file itself.
   *
   * Separate from the chart on purpose: the results a reader takes away should
   * be the ones the analysis produced, not a re-serialisation of whatever the
   * plot managed to parse. It needs no chart, so it works while one is loading.
   */
  const downloadFile = async () => {
    const filePath = selectedCluster?.file_path;
    if (!filePath) return;
    const { data, error } = await supabase.storage.from("scrna").download(filePath);
    if (error || !data) {
      setLoadError(error?.message ?? "the file could not be read");
      return;
    }
    const url = URL.createObjectURL(data);
    const a = document.createElement("a");
    a.href = url;
    a.download = filePath.split("/").pop() ?? "differential-expression.json";
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
  };

  const downloadCSV = () => {
    if (!chartData) return;

    const headers = ['gene', 'avg_log2FC', 'p_val', 'p_val_adj', 'pct.1', 'pct.2'];
    const csvContent = [
      headers.join(','),
      ...chartData.map(row =>
        headers.map(h => row[h as keyof GeneData]).join(',')
      )
    ].join('\n');

    const blob = new Blob([csvContent], { type: 'text/csv' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `DE_${selectedCluster?.cluster_id || 'cluster'}.csv`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
  };

  // Loading state
  if (loading) {
    return (
      <Box display="flex" justifyContent="center" alignItems="center" minHeight="400px">
        <CircularProgress />
        <Typography sx={{ ml: 2 }}>Loading differential expression data...</Typography>
      </Box>
    );
  }

  // No DE data available
  if (clusterList.length === 0) {
    return (
      <Box sx={{ p: 3 }}>
        <Alert severity="info">
          <Typography variant="subtitle1" fontWeight="bold">
            No Differential Expression Data Available
          </Typography>
          <Typography variant="body2" sx={{ mt: 1 }}>
            Differential expression analysis has not been computed for this dataset yet.
          </Typography>
        </Alert>
      </Box>
    );
  }

  // Compute summary stats
  const upregulated = chartData?.filter(d => d.p_val_adj < 0.05 && d.avg_log2FC > 1).length || 0;
  const downregulated = chartData?.filter(d => d.p_val_adj < 0.05 && d.avg_log2FC < -1).length || 0;

  return (
    <Box sx={{ p: 2 }}>
      {/* Header */}
      <Box display="flex" justifyContent="space-between" alignItems="center" mb={2}>
        <Typography variant="h5" fontWeight="bold">
          Differential Expression Analysis
        </Typography>
        <Tooltip title="Compare gene expression between a cluster and all other cells">
          <InfoOutlinedIcon color="action" />
        </Tooltip>
      </Box>

      <Typography variant="body2" color="text.secondary" mb={3}>
        Differential expression analysis identifies genes that are significantly up- or down-regulated
        in a specific cluster compared to all other cells. Statistical significance is determined using
        the Wilcoxon rank-sum test with Benjamini-Hochberg FDR correction.
      </Typography>

      {/* Which comparison */}
      <Paper sx={{ p: 2, mb: 3 }}>
        <Box display="flex" alignItems="center" gap={2} flexWrap="wrap">
          <FormControl sx={{ minWidth: 260 }}>
            <InputLabel id="cluster-select-label">Cell type</InputLabel>
            <Select
              labelId="cluster-select-label"
              value={selectedCluster?.cluster_id ?? ""}
              label="Cell type"
              onChange={(e) => {
                const next = e.target.value;
                // Keep the contrast if this cell type was also compared that
                // way, so moving down the list does not reset the question.
                const sameContrast = clusterList.find(
                  (row) => row.cluster_id === next &&
                    row.contrast === selectedCluster?.contrast,
                );
                setSelectedCluster(
                  sameContrast ??
                    clusterList.find((row) => row.cluster_id === next) ?? null,
                );
              }}
            >
              {cellTypes.map((cellType) => (
                <MenuItem key={cellType} value={cellType}>
                  {cellType}
                </MenuItem>
              ))}
            </Select>
          </FormControl>

          {/* Older one-vs-rest datasets have a single comparison per cell type
              and nothing to choose between, so they keep one selector. */}
          {contrastsForCellType.length > 1 && (
            <FormControl sx={{ minWidth: 300 }}>
              <InputLabel id="contrast-select-label">Comparison</InputLabel>
              <Select
                labelId="contrast-select-label"
                value={selectedCluster?.contrast ?? ""}
                label="Comparison"
                onChange={(e) => {
                  setSelectedCluster(
                    contrastsForCellType.find(
                      (row) => (row.contrast ?? "") === e.target.value,
                    ) ?? null,
                  );
                }}
              >
                {contrastsForCellType.map((row) => (
                  <MenuItem key={row.contrast ?? ""} value={row.contrast ?? ""}>
                    {contrastLabel(row)}
                    {significanceLabel(row) && (
                      <Typography component="span" variant="caption"
                                  color="text.secondary" sx={{ ml: 1 }}>
                        {significanceLabel(row)}
                      </Typography>
                    )}
                  </MenuItem>
                ))}
              </Select>
            </FormControl>
          )}

          {chartData && (
            <>
              <Chip
                label={`${upregulated} Upregulated`}
                color="error"
                variant="outlined"
                size="small"
              />
              <Chip
                label={`${downregulated} Downregulated`}
                color="primary"
                variant="outlined"
                size="small"
              />
              <Chip
                label={`${chartData.length} Total Genes`}
                variant="outlined"
                size="small"
              />
              <Tooltip title="Download the table as CSV">
                <IconButton onClick={downloadCSV} size="small">
                  <FileDownloadIcon />
                </IconButton>
              </Tooltip>
            </>
          )}

          {selectedCluster?.file_path && (
            <Button size="small" onClick={downloadFile}>
              Download results
            </Button>
          )}
        </Box>

        {selectedCluster && (
          <Typography variant="body2" color="text.secondary" sx={{ mt: 1.5 }}>
            {selectedCluster.group1 && selectedCluster.group2 ? (
              <>
                {selectedCluster.group1}
                {selectedCluster.n_group1 !== null &&
                  ` (${selectedCluster.n_group1.toLocaleString()} cells)`}
                {" against "}
                {selectedCluster.group2}
                {selectedCluster.n_group2 !== null &&
                  ` (${selectedCluster.n_group2.toLocaleString()} cells)`}
                {", in "}
                {selectedCluster.cluster_id}. {directionLabel(selectedCluster)}
              </>
            ) : (
              directionLabel(selectedCluster)
            )}
          </Typography>
        )}
      </Paper>

      {loadError && (
        <Alert severity="error" sx={{ mb: 3 }}>
          This comparison could not be loaded: {loadError}
        </Alert>
      )}

      {/* Considered, never run. The group sizes are what explain why. */}
      {selectedCluster && !selectedCluster.file_path && !dataLoading && (
        <Alert severity="info" sx={{ mb: 3 }}>
          <Typography variant="subtitle2" fontWeight="bold">
            This comparison was not run
          </Typography>
          <Typography variant="body2" sx={{ mt: 0.5 }}>
            {selectedCluster.group1 && selectedCluster.group2 ? (
              <>
                {selectedCluster.cluster_id} has{" "}
                {selectedCluster.n_group1?.toLocaleString() ?? "no"} cells in{" "}
                {selectedCluster.group1} and{" "}
                {selectedCluster.n_group2?.toLocaleString() ?? "no"} in{" "}
                {selectedCluster.group2} — too few on one side to compare.
              </>
            ) : (
              <>There are no results stored for {selectedCluster.cluster_id}.</>
            )}
          </Typography>
        </Alert>
      )}

      {/* Loading indicator for data */}
      {dataLoading && (
        <Box display="flex" justifyContent="center" alignItems="center" minHeight="200px">
          <CircularProgress size={24} />
          <Typography sx={{ ml: 2 }}>Loading cluster data...</Typography>
        </Box>
      )}

      {/* Volcano Plot */}
      {chartData && !dataLoading && (
        <>
          <Paper sx={{ p: 2, mb: 3 }}>
            <Typography variant="subtitle2" fontWeight="bold" mb={1}>
              Volcano Plot
            </Typography>
            <Typography variant="caption" color="text.secondary" display="block" mb={2}>
              Points above the horizontal line (p &lt; 0.05) and beyond vertical lines (|log2FC| &gt; 1)
              are considered significantly differentially expressed. Red = upregulated, Blue = downregulated.
            </Typography>
            <Box sx={{ display: "flex", justifyContent: "center" }}>
              <svg style={{ width: "100%", maxWidth: "800px", height: "500px" }} ref={chartRef}></svg>
            </Box>
          </Paper>

          {/* Data Table */}
          <Paper sx={{ p: 2 }}>
            <Typography variant="subtitle2" fontWeight="bold" mb={2}>
              Gene Table (sorted by adjusted p-value)
            </Typography>
            <DataTable rows={chartData} />
          </Paper>
        </>
      )}
    </Box>
  );
}
