import * as React from 'react';
import { useState, useEffect, useRef } from 'react';
import { DataGrid, GridColDef } from '@mui/x-data-grid';
import Paper from '@mui/material/Paper';
import { Database } from "@/lib/database.types";
import { createClientComponentClient } from "@supabase/auth-helpers-nextjs";
import Box from '@mui/material/Box';
import InputLabel from '@mui/material/InputLabel';
import MenuItem from '@mui/material/MenuItem';
import FormControl from '@mui/material/FormControl';
import Select from '@mui/material/Select';
import * as d3 from "d3";

type GeneData = {
  gene: string;
  p_val: number;
  avg_log2FC: number;
  pct1: number;
  pct2: number;
  p_val_adj: number;
  _row: string;
};

const columns: GridColDef[] = [
  { field: '_row', headerName: 'Gene Name', width: 200 },
  { field: 'p_val', headerName: 'p Value', width: 70 },
  { field: 'avg_log2FC', headerName: 'Avg. Log FC', width:  200 },
  { field: 'pct.1', headerName: '% of cells in Group1', width: 200 },
  { field: 'pct.2', headerName: '% of cells in Group2', width: 200 },
  { field: 'p_val_adj', headerName: 'Adj p Value', width: 200 }
];

export function DataTable({rows}:{rows: GeneData[]}) {

  return (
    <Paper sx={{ height: 800, width: '100%' }}>
      <DataGrid
        rows={rows ?? []}
        columns={columns}
        getRowId={(row) => row._row} 
        initialState={{
          pagination: {
            paginationModel: { pageSize: 30, page: 0 },
          },
        }}
        pageSizeOptions={[10, 20, 30, 100]}
        checkboxSelection
        sx={{ border: 0 }}
      />
    </Paper>
  );
}

export default function DifferentialExpressionAnalysis({ file_id }: { file_id: number }) {
    const [clusterList, setClusterList] = useState<{cluster_id : string, file_path: string}[]>([]);
    const [selectedCluster, setSelectedCluster] = useState<{cluster_id : string, file_path: string} | null>(null);
    const [chartData, setChartData] = useState<any>(null);
    const supabase = createClientComponentClient<Database>();
    const chartRef = useRef<SVGSVGElement | null>(null);

    useEffect(
      () => {
        const fetchData = async () => {
        const { data: clusterlabels, error} = await supabase
              .from("scrna_de")
              .select("cluster_id, file_path")
              .eq("dataset_id", file_id);
        
        setClusterList(clusterlabels || []);
        if (clusterlabels && clusterlabels.length > 0) {
          setSelectedCluster(clusterlabels[0]);
        }
        };
        fetchData();
      },
    [])

    useEffect(() => {
      if (!selectedCluster) return;
      const fetchData = async () => {
      const { data: storageData, error: storageError } = await supabase.storage
          .from("scrna")
          .download(selectedCluster.file_path);
      
      if (storageError) {
        console.error("Storage download error:", storageError);
        return;
      }
      const textData = await storageData.text();
      try {
        const jsonData = JSON.parse(textData);
        setChartData(jsonData);
      } catch (error) {
        console.error("Failed to parse JSON:", error);
      }
      };
      fetchData();
    },[selectedCluster]);

    useEffect(() => {
      if (!chartData || !chartRef.current) return;
      
      const svg = d3.select(chartRef.current);
      svg.selectAll("*").remove();

      const width = chartRef.current.clientWidth;
      const height = chartRef.current.clientHeight;
      const margin = { top: 20, right: 30, bottom: 50, left: 60 };
      const innerWidth = width - margin.left - margin.right;
      const innerHeight = height - margin.top - margin.bottom;

      const transformedData: {
        x: number;
        y: number;
        gene: string;
        p_val: number;
        avg_log2FC: number;
        pct1: number;
        pct2: number;
        p_val_adj: number;
        _row: string;
      }[] = chartData
      .filter((d: GeneData) => d.p_val_adj > 0)
      .map((d: GeneData) => ({
        ...d,
        x: +d.avg_log2FC,
        y: -Math.log10(+d.p_val_adj),
        }));
      
      const top20Genes = transformedData
        .sort((a, b) => a.p_val_adj - b.p_val_adj)
        .slice(0, 20);

      const xExtent = d3.extent(transformedData, d => d.x);
      const yMax = d3.max(transformedData, d => d.y);

      if (
        !xExtent ||
        typeof xExtent[0] !== "number" ||
        typeof xExtent[1] !== "number" ||
        typeof yMax !== "number"
      )
        return;

      const xScale = d3.scaleLinear()
        .domain([xExtent[0], xExtent[1]])
        .range([0, innerWidth]);
  
      const yScale = d3.scaleLinear()
        .domain([0, yMax])
        .range([innerHeight, 0]);
      
      const tooltip = d3.select("body").append("div")
        .style("position", "absolute")
        .style("padding", "6px")
        .style("background", "white")
        .style("border", "1px solid #ccc")
        .style("border-radius", "4px")
        .style("pointer-events", "none")
        .style("font-size", "12px")
        .style("display", "none");

      const plot = svg
        .attr("width", width)
        .attr("height", height)
        .append("g")
        .attr("transform", `translate(${margin.left},${margin.top})`);
      
        plot.append("g")
        .attr("transform", `translate(0, ${innerHeight})`)
        .call(d3.axisBottom(xScale));
  
      plot.append("g").call(d3.axisLeft(yScale));
  
      plot.selectAll("circle")
        .data(transformedData)
        .enter()
        .append("circle")
        .attr("cx", d => xScale(d.x))
        .attr("cy", d => yScale(d.y))
        .attr("r", 3)
        .attr("fill", d => {
          if (d.p_val_adj < 0.05 && d.x > 1) return "red";
          if (d.p_val_adj < 0.05 && d.x < -1) return "blue";
          return "gray";
        })
        .on("mouseover", (event, d) => {
          tooltip
            .style("display", "block")
            .html(`Gene: <strong>${d.gene}</strong>`);
        })
        .on("mousemove", (event) => {
          tooltip
            .style("left", (event.pageX + 10) + "px")
            .style("top", (event.pageY - 20) + "px");
        })
        .on("mouseout", () => {
          tooltip.style("display", "none");
        });
        // .each(function(d) {
        //   if (top20Genes.includes(d)) {
        //     plot.append("text")
        //       .attr("x", xScale(d.x))
        //       .attr("y", yScale(d.y))
        //       .attr("dy", -10) 
        //       .attr("dx", 5)  
        //       .attr("font-size", "10px")
        //       .attr("fill", "black")
        //       .text(d.gene); 
        //   }
        // });
      
    },[chartData])

    return (
      <>
      <div>
        <Box sx={{ minWidth: 120 }}>
          <FormControl fullWidth>
            <InputLabel id="select-bar">Cluster</InputLabel>
            <Select
              labelId="select-bar-label"
              id="select-bar-select"
              value={selectedCluster?.cluster_id || ""}
              label="Cluster"
              onChange={(e) => {
                const selected = clusterList.find(cluster => cluster.cluster_id === e.target.value);
                setSelectedCluster(selected || null);
              }}
            >
              {clusterList.map((cluster, index) => (
                <MenuItem key={index} value={cluster.cluster_id}>{cluster.cluster_id}</MenuItem>
              ))}
            </Select>
          </FormControl>
        </Box>
      </div>
      { chartData && <>
      <div id="volcano plot" style={{ display: "flex" }}>
        <div style={{ flex: 4, display: "flex", justifyContent: "center", alignItems: "center" }}>
            <svg style={{ height: "600px", width: "100%" }} ref={chartRef}></svg>
        </div>
      </div>
      <div>
      <DataTable rows={chartData}/>
      </div>
      </>
    }
    </>
    );
  }