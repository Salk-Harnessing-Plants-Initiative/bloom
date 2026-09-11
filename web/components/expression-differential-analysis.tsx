import * as React from 'react';
import { useState, useEffect, useMemo, useRef } from 'react';
import { DataGrid, GridColDef } from '@mui/x-data-grid';
import Paper from '@mui/material/Paper';
import Button from '@mui/material/Button';
import FormControlLabel from '@mui/material/FormControlLabel';
import Checkbox from '@mui/material/Checkbox';
import TextField from '@mui/material/TextField';
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

/** The two sides of this comparison, named. One answer, so the chart, the
 *  table and the tooltip cannot disagree about which group is which. */
export function groupNames(entry: DeEntry | null): { a: string; b: string } {
  return entry?.group1 && entry.group2
    ? { a: entry.group1, b: entry.group2 }
    : { a: entry?.cluster_id ?? "this cell type", b: "the rest" };
}

export const columnsFor = (entry: DeEntry | null): GridColDef[] => [
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
    headerName: `% in ${groupNames(entry).a}`,
    width: 120,
    renderCell: (params) => {
      const value = params.value as number;
      return <span>{(value * 100).toFixed(1)}%</span>;
    }
  },
  {
    field: 'pct.2',
    headerName: `% in ${groupNames(entry).b}`,
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

export function DataTable({ rows, entry }: { rows: GeneData[]; entry: DeEntry | null }) {
  return (
    <Paper sx={{ height: 500, width: '100%' }}>
      <DataGrid
        rows={rows ?? []}
        columns={columnsFor(entry)}
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

/** The cuts the analysis itself applied, so the panel agrees with the counts
 *  stored on the row rather than quietly using a stricter rule of its own. */
const DEFAULT_FDR_CUT = 0.05;
const DEFAULT_LOG2FC_CUT = 0.5;

/** Beyond this a fold change means "absent from one group" rather than a
 *  measured ratio, so it is not allowed to set the width of the plot. */
const OFF_SCALE_LOG2FC = 20;

/** A comparison in the dataset's analysis: a row of scrna_de.
 *
 * `tested` false means it was considered and skipped; the group sizes on the row
 * are what explain why, so it is shown rather than hidden. A null `contrast` is
 * an older one-vs-rest row, which has one selector and no groups to name.
 */
type DeEntry = {
  id: number;
  cluster_id: string | null;
  contrast: string | null;
  group1: string | null;
  group2: string | null;
  n_group1: number | null;
  n_group2: number | null;
  n_genes_tested: number | null;
  tested: boolean | null;
};

/** The analysis the comparisons belong to: a row of scrna_de_runs. */
type AnalysisRun = {
  id: number;
  method: string;
  completed_at: string | null;
  params: Database["public"]["Tables"]["scrna_de_runs"]["Row"]["params"];
};

/** A stored gene result, with its name from the dataset's gene catalogue. An
 *  infinite fold change arrives as text, since JSON has no number for it. */
type GeneRow = {
  log2fc: number | string | null;
  pvalue: number;
  fdr: number;
  pct_1: number | null;
  pct_2: number | null;
  scrna_genes: { gene_name: string } | null;
};

type Client = ReturnType<typeof createClientSupabaseClient>;

/** Rows per request when reading a comparison's genes. Reading stops at the
 *  first empty page, so a server-side row cap cannot cut the list short. */
const PAGE_ROWS = 1000;

/** The dataset's most recently completed analysis, or null when it has none. */
export async function fetchLatestRun(supabase: Client, datasetId: number): Promise<AnalysisRun | null> {
  const { data, error } = await supabase
    .from("scrna_de_runs")
    .select("id, method, params, completed_at")
    .eq("dataset_id", datasetId)
    .eq("status", "complete")
    .order("completed_at", { ascending: false })
    .limit(1);
  if (error) throw new Error(error.message);
  return (data?.[0] as AnalysisRun | undefined) ?? null;
}

/** An analysis's comparisons, by cell type and then contrast. */
export async function fetchComparisons(supabase: Client, runId: number): Promise<DeEntry[]> {
  const { data, error } = await supabase
    .from("scrna_de")
    .select("id, cluster_id, contrast, group1, group2, n_group1, n_group2, n_genes_tested, tested")
    .eq("run_id", runId);
  if (error) throw new Error(error.message);
  return ((data ?? []) as DeEntry[]).sort(
    (a, b) =>
      (a.cluster_id ?? "").localeCompare(b.cluster_id ?? "") ||
      (a.contrast ?? "").localeCompare(b.contrast ?? ""),
  );
}

/** A stored gene row in the shape the chart and the table use. A fold change
 *  the analysis could not compute is stored as null and read as NaN, which both
 *  leave out. */
export function toGeneData(row: GeneRow): GeneData {
  const gene = row.scrna_genes?.gene_name ?? "";
  return {
    gene,
    _row: gene,
    avg_log2FC: row.log2fc === null ? NaN : Number(row.log2fc),
    p_val: row.pvalue,
    p_val_adj: row.fdr,
    "pct.1": row.pct_1 ?? NaN,
    "pct.2": row.pct_2 ?? NaN,
  };
}

/** Every gene result of one comparison, a page at a time. */
export async function fetchGeneRows(supabase: Client, deId: number): Promise<GeneData[]> {
  const out: GeneData[] = [];
  for (let start = 0; ; start += PAGE_ROWS) {
    const { data, error } = await supabase
      .from("scrna_de_genes")
      .select("log2fc, pvalue, fdr, pct_1, pct_2, scrna_genes!inner(gene_name)")
      .eq("de_id", deId)
      .order("id")
      .range(start, start + PAGE_ROWS - 1);
    if (error) throw new Error(error.message);
    const rows = (data ?? []) as unknown as GeneRow[];
    if (rows.length === 0) return out;
    for (const row of rows) out.push(toGeneData(row));
  }
}

/** Each group's cells before depth matching, from the analysis notes, e.g.
 *  "pFACT 245, Col-0 172". Empty when the notes do not record this comparison. */
export function beforeDepthMatching(run: AnalysisRun | null, entry: DeEntry | null): string {
  if (!run || !entry?.cluster_id || !entry.contrast || !entry.group1 || !entry.group2) return "";
  const notes = (run.params as {
    notes?: { cells_before_depth_matching?: Record<string, Record<string, number>> };
  } | null)?.notes;
  const counts = notes?.cells_before_depth_matching?.[`${entry.cluster_id} / ${entry.contrast}`];
  if (!counts) return "";
  const fmt = new Intl.NumberFormat("en-US");
  return [entry.group1, entry.group2]
    .filter((group) => typeof counts[group] === "number")
    .map((group) => `${group} ${fmt.format(counts[group])}`)
    .join(", ");
}

/** What a comparison is called in the selectors. */
function contrastLabel(entry: DeEntry): string {
  return entry.contrast ?? "vs all other cells";
}

/** "15,430 genes tested", straight from the row, before any gene is fetched. */
export function testedLabel(entry: DeEntry): string {
  if (entry.tested === false || entry.n_genes_tested === 0) return "not tested";
  if (entry.n_genes_tested === null) return "";
  return `${new Intl.NumberFormat("en-US").format(entry.n_genes_tested)} genes tested`;
}

/** Genes passing both cuts, split by direction.
 *
 * The defaults are the analysis's own, so what the panel counts matches the
 * counts stored against the comparison. A stricter fold-change cut than the
 * analysis used would put a smaller number on screen than the one in the
 * selector, with nothing to say why.
 */
export function countSignificant(
  rows: { p_val_adj: number; avg_log2FC: number }[],
  fdrCut: number,
  lfcCut: number,
): { up: number; down: number; total: number } {
  let up = 0;
  let down = 0;
  for (const r of rows) {
    // No fold change means no direction, so it is neither up nor down.
    if (Number.isNaN(r.avg_log2FC)) continue;
    if (r.p_val_adj >= fdrCut || Math.abs(r.avg_log2FC) <= lfcCut) continue;
    if (r.avg_log2FC > 0) up++;
    else down++;
  }
  return { up, down, total: up + down };
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
  const [run, setRun] = useState<AnalysisRun | null>(null);
  const [clusterList, setClusterList] = useState<DeEntry[]>([]);
  const [selectedCluster, setSelectedCluster] = useState<DeEntry | null>(null);
  const [chartData, setChartData] = useState<GeneData[] | null>(null);
  const [loading, setLoading] = useState(true);
  const [dataLoading, setDataLoading] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [fdrCut, setFdrCut] = useState(DEFAULT_FDR_CUT);
  const [lfcCut, setLfcCut] = useState(DEFAULT_LOG2FC_CUT);
  const [onlySignificant, setOnlySignificant] = useState(false);
  // How many genes fall outside the default x-range, so the chart can say so.
  const [offScaleCount, setOffScaleCount] = useState(0);
  const supabase = createClientSupabaseClient();
  const chartRef = useRef<SVGSVGElement | null>(null);

  // The dataset's latest complete analysis, and its comparisons.
  useEffect(() => {
    let cancelled = false;
    const fetchData = async () => {
      setLoading(true);
      setLoadError(null);
      try {
        const latest = await fetchLatestRun(supabase, file_id);
        const rows = latest ? await fetchComparisons(supabase, latest.id) : [];
        if (cancelled) return;
        setRun(latest);
        setClusterList(rows);
        setSelectedCluster(rows.find((row) => row.tested !== false) ?? rows[0] ?? null);
      } catch (err) {
        if (!cancelled) setLoadError(err instanceof Error ? err.message : String(err));
      }
      if (!cancelled) setLoading(false);
    };
    fetchData();
    return () => {
      cancelled = true;
    };
  }, [file_id]);

  // The selected comparison's gene results.
  useEffect(() => {
    if (!selectedCluster) return;

    setChartData(null);
    if (selectedCluster.tested === false) {
      setDataLoading(false);
      setLoadError(null);
      return;
    }

    let cancelled = false;
    const fetchData = async () => {
      setDataLoading(true);
      setLoadError(null);
      try {
        const rows = await fetchGeneRows(supabase, selectedCluster.id);
        if (cancelled) return;
        setChartData(rows);
        if (rows.length === 0) setLoadError("no gene results are stored for it");
      } catch (err) {
        if (cancelled) return;
        setChartData(null);
        setLoadError(err instanceof Error ? err.message : String(err));
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

    // Height is the raw p-value, not the adjusted one. With this many genes
    // tested and few of them significant, almost every adjusted value is 1, so
    // -log10 of it is a flat line with a handful of spikes -- not a volcano.
    // Significance is still judged on the adjusted value, which is what colours
    // the points and where the line sits.
    const transformedData = chartData
      .filter((d: GeneData) => d.p_val > 0 && !isNaN(d.avg_log2FC))
      .map((d: GeneData) => ({
        ...d,
        x: +d.avg_log2FC,
        y: -Math.log10(+d.p_val),
      }));

    if (transformedData.length === 0) return;

    const significantGenes = transformedData
      .filter(d => d.p_val_adj < fdrCut && Math.abs(d.x) > lfcCut)
      .sort((a, b) => a.p_val_adj - b.p_val_adj)
      .slice(0, 10);

    // A gene absent from one group divides by nothing, and comes out at a fold
    // change of twenty or more. On this dataset that is a fifth of the genes,
    // none of them significant, scattered far enough to squash the real cloud
    // into a stripe. The axis covers what can be read; the rest is drawn at the
    // edge and counted underneath.
    const readable = transformedData
      .filter(d => Math.abs(d.x) <= OFF_SCALE_LOG2FC)
      .map(d => Math.abs(d.x));
    const xLimit = Math.max(
      lfcCut * 1.5,
      (d3.quantile(readable.sort(d3.ascending), 0.995) ?? 2) * 1.1,
    );
    setOffScaleCount(transformedData.filter(d => Math.abs(d.x) > xLimit).length);
    const xExtent: [number, number] = [-xLimit, xLimit];
    const yMax = d3.max(transformedData, d => d.y) || 10;

    // Symmetric x-axis
    const xMax = Math.max(Math.abs(xExtent[0]), Math.abs(xExtent[1]));

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

    // Everything that moves when the reader pans or zooms lives in here, and
    // is clipped rather than clamped: a gene keeps its real position and goes
    // out of view, instead of being drawn at a fold change it does not have.
    const clipId = `volcano-clip-${Math.random().toString(36).slice(2)}`;
    plot.append("defs").append("clipPath").attr("id", clipId)
      .append("rect").attr("width", innerWidth).attr("height", innerHeight);
    const content = plot.append("g").attr("clip-path", `url(#${clipId})`);

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

    // The height is a raw p-value but significance is judged on the adjusted
    // one, and the two do not line up at a fixed height: the line goes where
    // the weakest gene still passing the cut actually sits. No line when
    // nothing passes, rather than one drawn somewhere arbitrary.
    const passing = transformedData.filter(d => d.p_val_adj < fdrCut);
    if (passing.length > 0) {
      const sigY = d3.min(passing, d => d.y) as number;
      content.append("line")
        .attr("class", "fdr-line")
        .attr("x1", 0)
        .attr("x2", innerWidth)
        .attr("y1", yScale(sigY))
        .attr("y2", yScale(sigY))
        .attr("stroke", "#999")
        .attr("stroke-dasharray", "5,5")
        .attr("stroke-width", 1);
    }

    // Vertical lines at the reader's fold-change cut.
    content.append("line")
      .attr("class", "lfc-line-neg")
      .attr("x1", xScale(-lfcCut))
      .attr("x2", xScale(-lfcCut))
      .attr("y1", 0)
      .attr("y2", innerHeight)
      .attr("stroke", "#999")
      .attr("stroke-dasharray", "5,5")
      .attr("stroke-width", 1);

    content.append("line")
      .attr("class", "lfc-line-pos")
      .attr("x1", xScale(lfcCut))
      .attr("x2", xScale(lfcCut))
      .attr("y1", 0)
      .attr("y2", innerHeight)
      .attr("stroke", "#999")
      .attr("stroke-dasharray", "5,5")
      .attr("stroke-width", 1);

    // X-axis
    const xAxisG = plot.append("g")
      .attr("class", "x-axis")
      .attr("transform", `translate(0,${innerHeight})`);
    xAxisG.call(d3.axisBottom(xScale))
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
      .text("-Log10(p-value)");

    // Title
    plot.append("text")
      .attr("x", innerWidth / 2)
      .attr("y", -15)
      .attr("text-anchor", "middle")
      .attr("font-size", "16px")
      .attr("font-weight", "bold")
      .text(
        `Volcano Plot: ${groupNames(selectedCluster).a} vs ${groupNames(selectedCluster).b}` +
        (selectedCluster?.cluster_id ? `, in ${selectedCluster.cluster_id}` : ""),
      );

    // Points
    content.selectAll("circle")
      .data(transformedData)
      .enter()
      .append("circle")
      .attr("cx", d => xScale(d.x))
      .attr("cy", d => yScale(d.y))
      .attr("r", d => significantGenes.some(g => g.gene === d.gene) ? 5 : 3)
      .attr("fill", d => {
        if (d.p_val_adj < fdrCut && d.x > lfcCut) return "#c62828";
        if (d.p_val_adj < fdrCut && d.x < -lfcCut) return "#1565c0";
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
            % in ${groupNames(selectedCluster).a}: ${(d['pct.1'] * 100).toFixed(1)}%<br/>
            % in ${groupNames(selectedCluster).b}: ${(d['pct.2'] * 100).toFixed(1)}%
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
      content.append("text")
        .attr("class", "gene-label")
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

    // The default view is the readable range, so the cloud is not squashed by
    // the far outliers. Zooming out brings them back at their real positions.
    const zoom = d3.zoom<SVGSVGElement, unknown>()
      .scaleExtent([0.05, 20])
      .on("zoom", (event) => {
        const zx = event.transform.rescaleX(xScale);
        xAxisG.call(d3.axisBottom(zx));
        content.selectAll<SVGCircleElement, typeof transformedData[number]>("circle")
          .attr("cx", d => zx(d.x));
        content.selectAll<SVGTextElement, unknown>("text.gene-label")
          .attr("x", (_, i) => zx(significantGenes[i].x) + 8);
        content.select(".lfc-line-neg").attr("x1", zx(-lfcCut)).attr("x2", zx(-lfcCut));
        content.select(".lfc-line-pos").attr("x1", zx(lfcCut)).attr("x2", zx(lfcCut));
      });
    svg.call(zoom).on("dblclick.zoom", null);
    // Double-click is the way back, rather than a second zoom-in.
    svg.on("dblclick", () => svg.transition().duration(250).call(zoom.transform, d3.zoomIdentity));

    return () => {
      svg.on(".zoom", null).on("dblclick", null);
      tooltip.remove();
    };
  }, [chartData, selectedCluster, fdrCut, lfcCut]);

  /** Download the table as it stands, filtered or not, as CSV. */
  const downloadCSV = () => {
    if (!chartData) return;

    const headers = ['gene', 'avg_log2FC', 'p_val', 'p_val_adj', 'pct.1', 'pct.2'];
    const csvContent = [
      headers.join(','),
      ...tableRows.map(row =>
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

  if (loadError && clusterList.length === 0) {
    return (
      <Box sx={{ p: 3 }}>
        <Alert severity="error">
          The differential expression analysis could not be loaded: {loadError}
        </Alert>
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
  const { up: upregulated, down: downregulated } =
    countSignificant(chartData ?? [], fdrCut, lfcCut);
  const tableRows = onlySignificant
    ? (chartData ?? []).filter(
        d => d.p_val_adj < fdrCut && Math.abs(d.avg_log2FC) > lfcCut,
      )
    : chartData ?? [];

  return (
    <Box sx={{ p: 2 }}>
      {/* Header */}
      <Box display="flex" justifyContent="space-between" alignItems="center" mb={2}>
        <Typography variant="h5" fontWeight="bold">
          Differential Expression Analysis
        </Typography>
        <Tooltip title="Compare gene expression between the two groups a comparison names">
          <InfoOutlinedIcon color="action" />
        </Tooltip>
      </Box>

      <Typography variant="body2" color="text.secondary" mb={3}>
        Differential expression analysis identifies genes that are significantly up- or down-regulated
        in one group compared to the other. Statistical significance is determined using
        the Wilcoxon rank-sum test with Benjamini-Hochberg FDR correction.
        {run && ` Showing analysis ${run.id} (${run.method}).`}
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
                    {testedLabel(row) && (
                      <Typography component="span" variant="caption"
                                  color="text.secondary" sx={{ ml: 1 }}>
                        {testedLabel(row)}
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
        {beforeDepthMatching(run, selectedCluster) && (
          <Typography variant="caption" color="text.secondary" display="block" sx={{ mt: 0.5 }}>
            Cells compared after depth matching. Before it:{" "}
            {beforeDepthMatching(run, selectedCluster)}.
          </Typography>
        )}
      </Paper>

      {chartData && (
        <Paper sx={{ p: 2, mb: 3 }}>
          <Box display="flex" alignItems="center" gap={2} flexWrap="wrap">
            <Typography variant="subtitle2" fontWeight="bold">
              Significance
            </Typography>
            <TextField
              label="FDR below"
              type="number"
              size="small"
              value={fdrCut}
              onChange={(e) => setFdrCut(Math.max(0, Number(e.target.value)))}
              inputProps={{ step: 0.01, min: 0, max: 1 }}
              sx={{ width: 130 }}
            />
            <TextField
              label="|log2FC| above"
              type="number"
              size="small"
              value={lfcCut}
              onChange={(e) => setLfcCut(Math.max(0, Number(e.target.value)))}
              inputProps={{ step: 0.1, min: 0 }}
              sx={{ width: 150 }}
            />
            <FormControlLabel
              control={
                <Checkbox
                  size="small"
                  checked={onlySignificant}
                  onChange={(e) => setOnlySignificant(e.target.checked)}
                />
              }
              label="Table: significant only"
            />
            {(fdrCut !== DEFAULT_FDR_CUT || lfcCut !== DEFAULT_LOG2FC_CUT) && (
              <Button
                size="small"
                onClick={() => {
                  setFdrCut(DEFAULT_FDR_CUT);
                  setLfcCut(DEFAULT_LOG2FC_CUT);
                }}
              >
                Back to the analysis cuts
              </Button>
            )}
          </Box>
          <Typography variant="caption" color="text.secondary"
                      display="block" sx={{ mt: 1 }}>
            These start at the cuts the analysis itself used, so the counts here
            match the ones on the comparison you picked. Change them and
            everything below follows — the plot, the counts and the table.
          </Typography>
        </Paper>
      )}

      {loadError && (
        <Alert severity="error" sx={{ mb: 3 }}>
          This comparison could not be loaded: {loadError}
        </Alert>
      )}

      {/* Considered, never run. The group sizes are what explain why. */}
      {selectedCluster && selectedCluster.tested === false && !dataLoading && (
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

      {/* Nothing passes the cuts: say so, rather than leave an all-grey plot. */}
      {chartData && !dataLoading && chartData.length > 0 && upregulated + downregulated === 0 && (
        <Alert severity="info" sx={{ mb: 3 }}>
          No gene in this comparison has an adjusted p-value below {fdrCut} and a
          fold change beyond ±{lfcCut}, so every point is grey. Loosen the cuts
          above to see more.
        </Alert>
      )}

      {/* Volcano Plot */}
      {chartData && !dataLoading && (
        <>
          <Paper sx={{ p: 2, mb: 3 }}>
            <Typography variant="subtitle2" fontWeight="bold" mb={1}>
              Volcano Plot
            </Typography>
            <Typography variant="caption" color="text.secondary" display="block" mb={2}>
              Points beyond the vertical lines (|log2FC| &gt; {lfcCut}) and above the dashed line
              pass FDR &lt; {fdrCut}. Red = higher in {groupNames(selectedCluster).a}, blue = higher
              in {groupNames(selectedCluster).b}. Drag to pan, scroll to zoom.
            </Typography>
            {offScaleCount > 0 && (
              <Typography variant="caption" color="text.secondary" display="block" mb={1}>
                {offScaleCount.toLocaleString()} gene
                {offScaleCount === 1 ? " is" : "s are"} outside this view — zoom out to see
                {offScaleCount === 1 ? " it" : " them"}.
              </Typography>
            )}
            <Box sx={{ display: "flex", justifyContent: "center" }}>
              <svg style={{ width: "100%", maxWidth: "800px", height: "500px" }} ref={chartRef}></svg>
            </Box>
          </Paper>

          {/* Data Table */}
          <Paper sx={{ p: 2 }}>
            <Typography variant="subtitle2" fontWeight="bold" mb={2}>
              Gene table{onlySignificant
                ? ` — ${tableRows.length.toLocaleString()} passing the cuts`
                : ` — all ${tableRows.length.toLocaleString()} genes tested`}
            </Typography>
            <DataTable rows={tableRows} entry={selectedCluster} />
          </Paper>
        </>
      )}
    </Box>
  );
}
