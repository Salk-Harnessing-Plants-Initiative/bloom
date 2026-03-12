"use client";
import * as React from 'react';
import { useEffect, useRef, useState, useMemo, useLayoutEffect } from "react";
import { Database } from "@/lib/database.types";
import { createClientSupabaseClient } from "@/lib/supabase/client";
import CameraAltIcon from '@mui/icons-material/CameraAlt';
import FileDownloadIcon from '@mui/icons-material/FileDownload';
import html2canvas from "html2canvas";
import Button from '@mui/material/Button';
import CircularProgress from '@mui/material/CircularProgress';
import * as d3 from "d3";
import { Canvas, useFrame } from "@react-three/fiber";
import { OrbitControls } from "@react-three/drei";
import * as THREE from "three";

type Barcode = {
    barcode: String;
    cell_number: Number;
    cluster_id: Number;
    dataset_id: Number;
    id: Number;
    x: Number;
    y: Number;
    replicate: String | null;
};

export default function ExportScatterPlot({ file_id, file_name }: { file_id: number, file_name: string }) {
    const supabase = createClientSupabaseClient();
    const [barcode_data, setbarcodesData] = useState<Barcode[]>([]);
    const [clsuter_id, setClusterId] = useState<String[]>([]);
    const chartRef = useRef<SVGSVGElement | null>(null);
    const [loading, setLoading] = useState<boolean>(false);
    const [is3D, setIs3D] = useState<boolean>(false);
    // const [isDarkMode, setIsDarkMode] = useState<boolean>(false); // Dark mode disabled - always light mode
    const [isRotating, setIsRotating] = useState<boolean>(true);
    const [selectedClusters, setSelectedClusters] = useState<Set<string>>(new Set());
    const [selectedReplicates, setSelectedReplicates] = useState<Set<string>>(new Set());
    const [isTransitioning, setIsTransitioning] = useState<boolean>(false);

    // Handle mode switch with loading indicator
    const handleModeSwitch = () => {
        setIsTransitioning(true);
        // Use requestAnimationFrame to ensure the loading indicator renders before heavy computation
        requestAnimationFrame(() => {
            setTimeout(() => {
                setIs3D(!is3D);
                setIsTransitioning(false);
            }, 50);
        });
    };

    const toggleClusterSelection = (clusterId: string) => {
        setSelectedClusters(prev => {
            const newSet = new Set(prev);
            if (newSet.has(clusterId)) {
                newSet.delete(clusterId);
            } else {
                newSet.add(clusterId);
            }
            return newSet;
        });
    };

    const toggleReplicateSelection = (replicate: string) => {
        setSelectedReplicates(prev => {
            const newSet = new Set(prev);
            if (newSet.has(replicate)) {
                newSet.delete(replicate);
            } else {
                newSet.add(replicate);
            }
            return newSet;
        });
    };

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

    // Auto-switch dark mode based on 2D/3D mode - DISABLED (always light mode)
    // useEffect(() => {
    //     if (is3D) {
    //         setIsDarkMode(true); // Dark mode for 3D
    //     } else {
    //         setIsDarkMode(false); // Light mode for 2D
    //     }
    // }, [is3D]);

    // Memoize unique labels and color scale
    const uniqueLabels = useMemo(() => {
        if (barcode_data.length === 0) return [];
        return Array.from(new Set(barcode_data.map((d) => d.cluster_id.toString())));
    }, [barcode_data]);

    // Bright color palette for clusters
    const brightClusterColors = [
        "#FF6B6B", // Bright Red
        "#4ECDC4", // Bright Teal
        "#FFE66D", // Bright Yellow
        "#95E1D3", // Mint Green
        "#F38181", // Coral
        "#AA96DA", // Lavender
        "#FCBAD3", // Pink
        "#A8D8EA", // Sky Blue
        "#FF9F43", // Orange
        "#6BCB77", // Green
    ];

    const colorScale = useMemo(() => {
        return d3.scaleOrdinal<string, string>().domain(uniqueLabels).range(brightClusterColors);
    }, [uniqueLabels]);

    // Memoize unique replicates and replicate color scale
    const uniqueReplicates = useMemo(() => {
        if (barcode_data.length === 0) return [];
        const replicates = barcode_data
            .map((d) => d.replicate?.toString() || "Unknown")
            .filter((r) => r !== "Unknown");
        return Array.from(new Set(replicates)).sort();
    }, [barcode_data]);

    const replicateColorScale = useMemo(() => {
        return d3.scaleOrdinal<string, string>().domain(uniqueReplicates).range(replicateColors);
    }, [uniqueReplicates]);

    // Memoize processed data for 3D rendering
    const processedData = useMemo(() => {
        if (barcode_data.length === 0) return null;

        const xExtent = d3.extent(barcode_data, (d) => Number(d.x)) as [number, number];
        const yExtent = d3.extent(barcode_data, (d) => Number(d.y)) as [number, number];

        const xMin = xExtent[0];
        const xMax = xExtent[1];
        const yMin = yExtent[0];
        const yMax = yExtent[1];

        const xRange = xMax - xMin;
        const yRange = yMax - yMin;
        const maxRange = Math.max(xRange, yRange);
        const scale = 30 / maxRange;

        // Create cluster to neon color mapping
        const clusterToNeonColor: { [key: string]: string } = {};
        uniqueLabels.forEach((cluster, idx) => {
            clusterToNeonColor[cluster] = neonColors[idx % neonColors.length];
        });

        // Pre-compute positions and colors for all points
        const positions = new Float32Array(barcode_data.length * 3);
        const colors = new Float32Array(barcode_data.length * 3);
        const clusterIds: string[] = [];
        const replicateIds: string[] = [];

        barcode_data.forEach((d, i) => {
            const x = (Number(d.x) - (xMin + xMax) / 2) * scale;
            const y = (Number(d.y) - (yMin + yMax) / 2) * scale;
            const z = (Math.sin(x * 0.3) + Math.cos(y * 0.3)) * 2;

            positions[i * 3] = x;
            positions[i * 3 + 1] = y;
            positions[i * 3 + 2] = z;

            const clusterId = d.cluster_id.toString();
            clusterIds.push(clusterId);

            const replicateId = d.replicate?.toString() || "Unknown";
            replicateIds.push(replicateId);

            const colorHex = clusterToNeonColor[clusterId];
            const color = new THREE.Color(colorHex);
            colors[i * 3] = color.r;
            colors[i * 3 + 1] = color.g;
            colors[i * 3 + 2] = color.b;
        });

        // Pre-compute 2D positions (z = 0)
        const positions2D = new Float32Array(barcode_data.length * 3);
        const colors2D = new Float32Array(barcode_data.length * 3);

        barcode_data.forEach((d, i) => {
            const x = (Number(d.x) - (xMin + xMax) / 2) * scale;
            const y = (Number(d.y) - (yMin + yMax) / 2) * scale;

            positions2D[i * 3] = x;
            positions2D[i * 3 + 1] = y;
            positions2D[i * 3 + 2] = 0;

            const clusterId = d.cluster_id.toString();
            const colorHex = colorScale(clusterId);
            const color = new THREE.Color(colorHex);
            colors2D[i * 3] = color.r;
            colors2D[i * 3 + 1] = color.g;
            colors2D[i * 3 + 2] = color.b;
        });

        return {
            positions,
            colors,
            positions2D,
            colors2D,
            clusterIds,
            replicateIds,
            clusterToNeonColor,
            count: barcode_data.length
        };
    }, [barcode_data, uniqueLabels, colorScale]);

    return (
        <>
            {loading ? (
                <div style={{ display: 'flex', justifyContent: 'center', alignItems: 'center', height: '100vh' }}>
                    <CircularProgress />
                </div>
            ) : (
                <div style={{ minHeight: '800px', backgroundColor: '#ffffff' }}>
                    <div style={{ marginLeft: '30px', margin: '10px', padding: '10px', display: 'flex', gap: '10px', flexWrap: 'wrap' }}>
                        <Button
                            onClick={handleModeSwitch}
                            variant="contained"
                            style={{ marginRight: '10px' }}
                            disabled={isTransitioning}
                        >
                            {isTransitioning ? "Loading..." : (is3D ? "Switch to 2D" : "Switch to 3D")}
                        </Button>
                        {/* Dark mode button disabled - always light mode
                        <Button
                            onClick={() => setIsDarkMode(!isDarkMode)}
                            variant="contained"
                            style={{ marginRight: '10px' }}
                        >
                            {isDarkMode ? "Light Mode" : "Dark Mode"}
                        </Button>
                        */}
                        {is3D && (
                            <Button
                                onClick={() => setIsRotating(!isRotating)}
                                variant="contained"
                                style={{ marginRight: '10px' }}
                            >
                                {isRotating ? "Stop Rotation" : "Start Rotation"}
                            </Button>
                        )}
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

                    <div id="UMAP_plot" style={{ display: "flex", width: "100%", height: "800px", overflow: "hidden", position: "relative" }}>
                        {isTransitioning && (
                            <div style={{
                                position: "absolute",
                                top: 0,
                                left: 0,
                                right: 0,
                                bottom: 0,
                                backgroundColor: "rgba(255, 255, 255, 0.8)",
                                display: "flex",
                                justifyContent: "center",
                                alignItems: "center",
                                zIndex: 100
                            }}>
                                <CircularProgress size={60} />
                            </div>
                        )}
                        {is3D ? (
                            <>
                                <div style={{ flex: 4, display: "flex", justifyContent: "center", alignItems: "center", height: "800px", width: "100%", position: "relative" }}>
                                    <Canvas
                                        camera={{ position: [0, 0, 50], fov: 50 }}
                                        style={{ width: '100%', height: '100%', position: 'absolute', top: 0, left: 0 }}
                                    >
                                        {/* Always light mode background */}
                                        <color attach="background" args={[0.95, 0.95, 0.95]} />
                                        <ambientLight intensity={0.6} />
                                        <pointLight position={[10, 10, 10]} intensity={0.8} />
                                        <RotatingPoints
                                            processedData={processedData}
                                            isRotating={isRotating}
                                            selectedClusters={selectedClusters}
                                            selectedReplicates={selectedReplicates}
                                            onClusterClick={toggleClusterSelection}
                                            colorScale={colorScale}
                                        />
                                        <OrbitControls enableDamping dampingFactor={0.05} />
                                    </Canvas>
                                </div>
                                {/* Legend panels container */}
                                <div style={{ display: "flex", flexDirection: "column", gap: "10px", width: "220px" }}>
                                    {/* Cell Type Legend */}
                                    <div style={{
                                        padding: "10px",
                                        maxHeight: "380px",
                                        overflowY: "auto",
                                        border: "1px solid #e0e0e0",
                                        backgroundColor: "#ffffff",
                                        borderRadius: "8px"
                                    }}>
                                        <div style={{
                                            fontSize: "16px",
                                            fontWeight: "bold",
                                            marginBottom: "10px",
                                            color: "black"
                                        }}>
                                            Cell Type:
                                        </div>
                                        {uniqueLabels.map((label) => {
                                            const isSelected = selectedClusters.has(label);
                                            const isAnySelected = selectedClusters.size > 0;
                                            return (
                                                <div
                                                    key={label}
                                                    style={{
                                                        display: "flex",
                                                        alignItems: "center",
                                                        marginTop: "10px",
                                                        cursor: "pointer",
                                                        opacity: isAnySelected && !isSelected ? 0.3 : 1,
                                                        transition: "opacity 0.2s"
                                                    }}
                                                    onClick={() => toggleClusterSelection(label)}
                                                >
                                                    <div style={{
                                                        width: "20px",
                                                        height: "30px",
                                                        backgroundColor: colorScale(label) as string,
                                                        border: isSelected ? "2px solid #333" : "none",
                                                        boxSizing: "border-box"
                                                    }}></div>
                                                    <span style={{
                                                        marginLeft: "5px",
                                                        fontSize: "14px",
                                                        color: "black",
                                                        fontWeight: isSelected ? "bold" : "normal"
                                                    }}>
                                                        {label}
                                                    </span>
                                                </div>
                                            );
                                        })}
                                    </div>
                                    {/* Replicate Legend */}
                                    {uniqueReplicates.length > 0 && (
                                        <div style={{
                                            padding: "10px",
                                            maxHeight: "380px",
                                            overflowY: "auto",
                                            border: "1px solid #e0e0e0",
                                            backgroundColor: "#ffffff",
                                            borderRadius: "8px"
                                        }}>
                                            <div style={{
                                                fontSize: "16px",
                                                fontWeight: "bold",
                                                marginBottom: "10px",
                                                color: "black"
                                            }}>
                                                Replicate:
                                            </div>
                                            {uniqueReplicates.map((replicate) => {
                                                const isSelected = selectedReplicates.has(replicate);
                                                const isAnySelected = selectedReplicates.size > 0;
                                                return (
                                                    <div
                                                        key={replicate}
                                                        style={{
                                                            display: "flex",
                                                            alignItems: "center",
                                                            marginTop: "10px",
                                                            cursor: "pointer",
                                                            opacity: isAnySelected && !isSelected ? 0.3 : 1,
                                                            transition: "opacity 0.2s"
                                                        }}
                                                        onClick={() => toggleReplicateSelection(replicate)}
                                                    >
                                                        <div style={{
                                                            width: "20px",
                                                            height: "30px",
                                                            backgroundColor: replicateColorScale(replicate) as string,
                                                            border: isSelected ? "2px solid #333" : "none",
                                                            boxSizing: "border-box"
                                                        }}></div>
                                                        <span style={{
                                                            marginLeft: "5px",
                                                            fontSize: "14px",
                                                            color: "black",
                                                            fontWeight: isSelected ? "bold" : "normal"
                                                        }}>
                                                            {replicate}
                                                        </span>
                                                    </div>
                                                );
                                            })}
                                        </div>
                                    )}
                                </div>
                            </>
                        ) : (
                            <>
                                <div style={{ flex: 4, display: "flex", justifyContent: "center", alignItems: "center", height: "800px", width: "100%", position: "relative" }}>
                                    <Canvas
                                        camera={{ position: [0, 0, 50], fov: 50 }}
                                        style={{ width: '100%', height: '100%', position: 'absolute', top: 0, left: 0 }}
                                    >
                                        {/* Always light mode background */}
                                        <color attach="background" args={[0.95, 0.95, 0.95]} />
                                        <ambientLight intensity={0.6} />
                                        <pointLight position={[10, 10, 10]} intensity={0.8} />
                                        <Static2DPoints
                                            processedData={processedData}
                                            colorScale={colorScale}
                                            selectedClusters={selectedClusters}
                                            selectedReplicates={selectedReplicates}
                                            onClusterClick={toggleClusterSelection}
                                        />
                                        <OrbitControls
                                            enableDamping
                                            dampingFactor={0.05}
                                            enableRotate={false}
                                            enableZoom={true}
                                            enablePan={true}
                                            mouseButtons={{
                                                LEFT: THREE.MOUSE.PAN,
                                                MIDDLE: THREE.MOUSE.DOLLY,
                                                RIGHT: THREE.MOUSE.ROTATE
                                            }}
                                        />
                                    </Canvas>
                                </div>
                                {/* Legend panels container */}
                                <div style={{ display: "flex", flexDirection: "column", gap: "10px", width: "220px" }}>
                                    {/* Cell Type Legend */}
                                    <div style={{
                                        padding: "10px",
                                        maxHeight: "380px",
                                        overflowY: "auto",
                                        border: "1px solid #e0e0e0",
                                        backgroundColor: "#ffffff",
                                        borderRadius: "8px"
                                    }}>
                                        <div style={{
                                            fontSize: "16px",
                                            fontWeight: "bold",
                                            marginBottom: "10px",
                                            color: "black"
                                        }}>
                                            Cell Type:
                                        </div>
                                        {uniqueLabels.map((label) => {
                                            const isSelected = selectedClusters.has(label);
                                            const isAnySelected = selectedClusters.size > 0;
                                            return (
                                                <div
                                                    key={label}
                                                    style={{
                                                        display: "flex",
                                                        alignItems: "center",
                                                        marginTop: "10px",
                                                        cursor: "pointer",
                                                        opacity: isAnySelected && !isSelected ? 0.3 : 1,
                                                        transition: "opacity 0.2s"
                                                    }}
                                                    onClick={() => toggleClusterSelection(label)}
                                                >
                                                    <div style={{
                                                        width: "20px",
                                                        height: "30px",
                                                        backgroundColor: colorScale(label) as string,
                                                        border: isSelected ? "2px solid #333" : "none",
                                                        boxSizing: "border-box"
                                                    }}></div>
                                                    <span style={{
                                                        marginLeft: "5px",
                                                        fontSize: "14px",
                                                        color: "black",
                                                        fontWeight: isSelected ? "bold" : "normal"
                                                    }}>
                                                        {label}
                                                    </span>
                                                </div>
                                            );
                                        })}
                                    </div>
                                    {/* Replicate Legend */}
                                    {uniqueReplicates.length > 0 && (
                                        <div style={{
                                            padding: "10px",
                                            maxHeight: "380px",
                                            overflowY: "auto",
                                            border: "1px solid #e0e0e0",
                                            backgroundColor: "#ffffff",
                                            borderRadius: "8px"
                                        }}>
                                            <div style={{
                                                fontSize: "16px",
                                                fontWeight: "bold",
                                                marginBottom: "10px",
                                                color: "black"
                                            }}>
                                                Replicate:
                                            </div>
                                            {uniqueReplicates.map((replicate) => {
                                                const isSelected = selectedReplicates.has(replicate);
                                                const isAnySelected = selectedReplicates.size > 0;
                                                return (
                                                    <div
                                                        key={replicate}
                                                        style={{
                                                            display: "flex",
                                                            alignItems: "center",
                                                            marginTop: "10px",
                                                            cursor: "pointer",
                                                            opacity: isAnySelected && !isSelected ? 0.3 : 1,
                                                            transition: "opacity 0.2s"
                                                        }}
                                                        onClick={() => toggleReplicateSelection(replicate)}
                                                    >
                                                        <div style={{
                                                            width: "20px",
                                                            height: "30px",
                                                            backgroundColor: replicateColorScale(replicate) as string,
                                                            border: isSelected ? "2px solid #333" : "none",
                                                            boxSizing: "border-box"
                                                        }}></div>
                                                        <span style={{
                                                            marginLeft: "5px",
                                                            fontSize: "14px",
                                                            color: "black",
                                                            fontWeight: isSelected ? "bold" : "normal"
                                                        }}>
                                                            {replicate}
                                                        </span>
                                                    </div>
                                                );
                                            })}
                                        </div>
                                    )}
                                </div>
                            </>
                        )}
                    </div>
                </div>
            )}
        </>
    )
}

// Neon color palette for clusters
const neonColors = [
    "#ff006e", // Neon Pink
    "#00f5ff", // Neon Cyan
    "#ffbe0b", // Neon Yellow
    "#8338ec", // Neon Purple
    "#3a86ff", // Neon Blue
    "#fb5607", // Neon Orange
    "#06ffa5", // Neon Green
    "#ff006e", // Neon Magenta
    "#00bbf9", // Neon Sky Blue
    "#f72585", // Neon Rose
];

// Bright color palette for replicates (distinct from cluster colors)
const replicateColors = [
    "#00D9FF", // Bright Cyan
    "#FF6B9D", // Bright Pink
    "#00FF88", // Bright Green
    "#FFB800", // Bright Gold
    "#B388FF", // Bright Purple
    "#FF5252", // Bright Red
    "#64FFDA", // Bright Aqua
    "#FFFF00", // Bright Yellow
    "#FF80AB", // Bright Rose
    "#69F0AE", // Bright Mint
];

// 3D Rotating Points Component
function RotatingPoints({
    processedData,
    isRotating,
    selectedClusters,
    selectedReplicates,
    onClusterClick,
    colorScale
}: {
    processedData: {
        positions: Float32Array,
        colors: Float32Array,
        clusterIds: string[],
        replicateIds: string[],
        clusterToNeonColor: { [key: string]: string },
        count: number
    } | null,
    isRotating: boolean,
    selectedClusters: Set<string>,
    selectedReplicates: Set<string>,
    onClusterClick: (clusterId: string) => void,
    colorScale: d3.ScaleOrdinal<string, string>
}) {
    const groupRef = useRef<THREE.Group>(null);

    // Rotate the entire group only if isRotating is true
    useFrame((state, delta) => {
        if (groupRef.current && isRotating) {
            groupRef.current.rotation.y += delta * 0.15;
        }
    });

    if (!processedData || processedData.count === 0) return null;

    const isAnyClustersSelected = selectedClusters.size > 0;
    const isAnyReplicatesSelected = selectedReplicates.size > 0;

    return (
        <group ref={groupRef}>
            {processedData.clusterIds.map((clusterId, i) => {
                const x = processedData.positions[i * 3];
                const y = processedData.positions[i * 3 + 1];
                const z = processedData.positions[i * 3 + 2];
                const replicateId = processedData.replicateIds[i];

                // Combined selection logic:
                // - If only clusters selected: filter by cluster
                // - If only replicates selected: filter by replicate
                // - If both selected: AND logic (must match both)
                const clusterMatch = !isAnyClustersSelected || selectedClusters.has(clusterId);
                const replicateMatch = !isAnyReplicatesSelected || selectedReplicates.has(replicateId);
                const isSelected = clusterMatch && replicateMatch;
                const shouldGrey = (isAnyClustersSelected || isAnyReplicatesSelected) && !isSelected;

                const colorHex = colorScale(clusterId);

                return (
                    <mesh
                        key={i}
                        position={[x, y, z]}
                        onClick={(e) => {
                            e.stopPropagation();
                            onClusterClick(clusterId);
                        }}
                    >
                        <sphereGeometry args={[0.25, 12, 12]} />
                        <meshBasicMaterial
                            color={shouldGrey ? "#cccccc" : colorHex}
                            transparent={true}
                            opacity={shouldGrey ? 0.2 : 1}
                        />
                    </mesh>
                );
            })}
        </group>
    );
}

// 2D Static Points Component
function Static2DPoints({
    processedData,
    colorScale,
    selectedClusters,
    selectedReplicates,
    onClusterClick
}: {
    processedData: {
        positions2D: Float32Array,
        colors2D: Float32Array,
        clusterIds: string[],
        replicateIds: string[],
        count: number
    } | null,
    colorScale: d3.ScaleOrdinal<string, string>,
    selectedClusters: Set<string>,
    selectedReplicates: Set<string>,
    onClusterClick: (clusterId: string) => void
}) {
    if (!processedData || processedData.count === 0) return null;

    const isAnyClustersSelected = selectedClusters.size > 0;
    const isAnyReplicatesSelected = selectedReplicates.size > 0;

    return (
        <group>
            {processedData.clusterIds.map((clusterId, i) => {
                const x = processedData.positions2D[i * 3];
                const y = processedData.positions2D[i * 3 + 1];
                const replicateId = processedData.replicateIds[i];

                // Combined selection logic:
                // - If only clusters selected: filter by cluster
                // - If only replicates selected: filter by replicate
                // - If both selected: AND logic (must match both)
                const clusterMatch = !isAnyClustersSelected || selectedClusters.has(clusterId);
                const replicateMatch = !isAnyReplicatesSelected || selectedReplicates.has(replicateId);
                const isSelected = clusterMatch && replicateMatch;
                const shouldGrey = (isAnyClustersSelected || isAnyReplicatesSelected) && !isSelected;

                const colorHex = colorScale(clusterId);

                return (
                    <mesh
                        key={i}
                        position={[x, y, 0]}
                        onClick={(e) => {
                            e.stopPropagation();
                            onClusterClick(clusterId);
                        }}
                    >
                        <sphereGeometry args={[0.12, 8, 8]} />
                        <meshBasicMaterial
                            color={shouldGrey ? "#cccccc" : colorHex}
                            transparent={true}
                            opacity={shouldGrey ? 0.2 : 1}
                        />
                    </mesh>
                );
            })}
        </group>
    );
}
