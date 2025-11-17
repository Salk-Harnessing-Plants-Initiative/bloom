import React, { useEffect, useRef, useState } from "react";
import Box from '@mui/material/Box';
import GeneDrillDownBoxPots from './expression-gene-drilldown-boxplot'
import GeneDrillUMAP from './expression-gene-drilldown-scatterplot'
import * as d3 from "d3";

type Barcode = {
    cluster_id: string | null;  // Changed from number to string to match database type
    barcode: string | null;
    cell_number: number;
    x: number | null;
    y: number | null;
}

type GeneData = {
    gene_id: number;
    gene_name: string;
    counts: [{
        key: number
        value: number
    }],
    data: Barcode[] | null;
}

type ScatterPlot = {
    cluster_id: string | null;  // Changed from number to string to match database type
    barcode: string | null;
    x: number | null;
    y: number | null;
    expression: number;
}

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

export default function GeneDrillDown({ geneData }: { geneData: GeneData }) {    
    const [plotStats, setPlostStats] = useState<PlotStats[]>([]);
    const [scatterPlot, setScatterPlot] = useState<ScatterPlot[]>([]);

    useEffect(()=>{

        const counts_obj = geneData.counts.reduce<{[key:number]:number}>((acc, item) => {
            const key = Number(Object.keys(item)[0]); 
            const value = item[key as unknown as keyof typeof item]
            acc[key] = value;
            return acc; 
        }, {} as {[key:number]:number});

        const stats_data = geneData?.data?.reduce<{ [clusterid: string]: { barcodes: string[]; expression: number[]; points: {expression: number, barcode: string}[] } }>((acc: { [clusterId: string]: { barcodes: string[]; expression: number[]; points: {expression: number, barcode: string}[] } }, item: Barcode, ) => {
            if (item.cluster_id !== null && !acc[item.cluster_id]) {
            acc[item.cluster_id] = { barcodes: [], expression: [], points : [] };
            }
        
            if (item.cluster_id !== null) {
            acc[item.cluster_id]?.barcodes.push(item.barcode ? item.barcode :  "N/A");
            acc[item.cluster_id]?.expression.push(counts_obj[item.cell_number + 1]);
            acc[item.cluster_id]?.points.push({expression: counts_obj[item.cell_number + 1], barcode: item.barcode ? item.barcode : "N/A" })
            }
        
            return acc;
        }, {} as { [clusterid: string]: { barcodes: string[]; expression: number[]; points:[]} });

        const scatterPlot : ScatterPlot[] = geneData?.data?.map(item => ({
            cluster_id: item.cluster_id,
            barcode: item.barcode,
            x: item.x,
            y: item.y,
            expression: counts_obj[item.cell_number + 1]
        })) || []


        const stats = stats_data?Object.keys(stats_data).map((clusterId: string) => {
            const expression_levels = stats_data[clusterId].expression
            const sorted_exp_val = expression_levels.filter((d): d is number => d !== null).sort(d3.ascending);
            
            const q1 = d3.quantile(sorted_exp_val, 0.25) || 0;
            const median = d3.quantile(sorted_exp_val, 0.5) || 0;
            const q3 = d3.quantile(sorted_exp_val, 0.75) || 0;
        
            const interQuantileRange = q3 - q1;
            const minVal = d3.min(sorted_exp_val) || 0;
            const maxVal = d3.max(sorted_exp_val) || 0;

            const lowerWhisker = q1 - 1.5 * interQuantileRange || 0
            const upperWhisker = q3 + 1.5 * interQuantileRange || 0;
                    
             return {
                 key: `${clusterId}`,
                 expression: expression_levels,
                 barcodes: stats_data[clusterId].barcodes,
                 points: stats_data[clusterId].points,
                 value: {
                     q1: q1,
                     median: median,
                     q3: q3,
                     min: minVal,
                     max: maxVal,
                     lowerWhisker: lowerWhisker,
                     upperWhisker: upperWhisker,
                 }
             };

        }) : []

        setPlostStats(stats);
        setScatterPlot(scatterPlot);
    },[])



    return (
        <>
            <Box
                sx={{
                    display: 'flex',
                    flexDirection: 'column',
                    gap: 5,
                    width: '100%',
                    alignItems: 'center',
                    padding: 2,
                }}
            >
                <Box
                    sx={{
                        width: '100%',
                        height: '700px',
                    }}
                >
                    <GeneDrillDownBoxPots BoxPlotData={plotStats} geneName = {geneData.gene_name}/>
                </Box>
                <Box
                    sx={{
                        width: '100%',
                        height: '900px',
                    }}
                >
                    <GeneDrillUMAP scatterPlot = {scatterPlot} geneName={geneData.gene_name}/>
                </Box>
            </Box>


        </>
    )
}
