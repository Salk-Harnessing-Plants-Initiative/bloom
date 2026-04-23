"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import * as THREE from "three";
import { OrbitControls } from "three/examples/jsm/controls/OrbitControls.js";
import Box from "@mui/material/Box";
import ToggleButtonGroup from "@mui/material/ToggleButtonGroup";
import ToggleButton from "@mui/material/ToggleButton";
import Typography from "@mui/material/Typography";

import {
  fetchCells,
  fetchClusters,
  fetchDataset,
  type CellArraysRow,
} from "@/components/expression-lib/scrna-client";
import type { Database } from "@/lib/database.types";

type Cluster = Database["public"]["Tables"]["scrna_clusters"]["Row"];
type Dataset = Database["public"]["Tables"]["scrna_datasets"]["Row"];

const POINT_SIZE = 0.02;
const AXIS_COUNT = 5;
const NORMALIZE_PADDING = 1.05;

type AxisKey = "pc1" | "pc2" | "pc3" | "pc4" | "pc5";
const AXIS_KEYS: readonly AxisKey[] = ["pc1", "pc2", "pc3", "pc4", "pc5"];

export interface ExpressionPca3dProps {
  datasetId: number;
  hiddenClusters?: ReadonlySet<number>;
  height?: number;
  onDataLoaded?: (ctx: {
    dataset: Dataset;
    clusters: Cluster[];
    cellCount: number;
  }) => void;
}

function hexToRgb(hex: string | null): [number, number, number] {
  if (!hex) return [0.5, 0.5, 0.5];
  const trimmed = hex.replace(/^#/, "");
  if (trimmed.length !== 6) return [0.5, 0.5, 0.5];
  const n = parseInt(trimmed, 16);
  if (Number.isNaN(n)) return [0.5, 0.5, 0.5];
  return [((n >> 16) & 0xff) / 255, ((n >> 8) & 0xff) / 255, (n & 0xff) / 255];
}

/**
 * PCA 3D scatter view using vanilla three.js (@react-three/fiber is not
 * installed despite appearing in package.json). User picks which three PCs
 * to render on X/Y/Z via a ToggleButtonGroup. Points are cluster-colored;
 * OrbitControls handles rotate/zoom/pan.
 */
export function ExpressionPca3d({
  datasetId,
  hiddenClusters,
  height = 600,
  onDataLoaded,
}: ExpressionPca3dProps) {
  const mountRef = useRef<HTMLDivElement | null>(null);
  const [dataset, setDataset] = useState<Dataset | null>(null);
  const [clusters, setClusters] = useState<Cluster[]>([]);
  const [cells, setCells] = useState<CellArraysRow[] | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [axisX, setAxisX] = useState<AxisKey>("pc1");
  const [axisY, setAxisY] = useState<AxisKey>("pc2");
  const [axisZ, setAxisZ] = useState<AxisKey>("pc3");

  // ---- data load ------------------------------------------------------------
  useEffect(() => {
    let cancelled = false;
    setLoadError(null);
    setCells(null);
    (async () => {
      try {
        const [ds, cl, cs] = await Promise.all([
          fetchDataset(datasetId),
          fetchClusters(datasetId),
          fetchCells(datasetId),
        ]);
        if (cancelled) return;
        if (!ds) {
          setLoadError(`Dataset ${datasetId} not found.`);
          return;
        }
        setDataset(ds);
        setClusters(cl);
        setCells(cs);
        onDataLoaded?.({ dataset: ds, clusters: cl, cellCount: cs.length });
      } catch (err) {
        if (!cancelled) {
          setLoadError(err instanceof Error ? err.message : String(err));
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [datasetId, onDataLoaded]);

  // compute if any cell has PC data
  const hasPcaData = useMemo(() => {
    if (!cells || cells.length === 0) return false;
    return cells[0].pc1 !== null;
  }, [cells]);

  // color buffer (depends on clusters + hidden state)
  const colorBuffer = useMemo(() => {
    if (!cells) return null;
    const hidden = hiddenClusters ?? new Set<number>();
    const paletteRgb = new Map<number, [number, number, number]>();
    for (const c of clusters) paletteRgb.set(c.ordinal, hexToRgb(c.color));
    const out = new Float32Array(cells.length * 3);
    for (let i = 0; i < cells.length; i++) {
      const ord = cells[i].cluster_ordinal;
      const rgb = hidden.has(ord)
        ? [0, 0, 0] // hidden cells render as pure black against dark background
        : paletteRgb.get(ord) ?? [0.5, 0.5, 0.5];
      out[i * 3] = rgb[0];
      out[i * 3 + 1] = rgb[1];
      out[i * 3 + 2] = rgb[2];
    }
    return out;
  }, [cells, clusters, hiddenClusters]);

  // position buffer (depends on axis selection)
  const positionBuffer = useMemo(() => {
    if (!cells || !hasPcaData) return null;
    const n = cells.length;
    const out = new Float32Array(n * 3);
    let maxAbs = 0;
    const getAxisValue = (row: CellArraysRow, key: AxisKey): number => {
      const v = row[key];
      return v ?? 0;
    };
    for (let i = 0; i < n; i++) {
      const x = getAxisValue(cells[i], axisX);
      const y = getAxisValue(cells[i], axisY);
      const z = getAxisValue(cells[i], axisZ);
      out[i * 3] = x;
      out[i * 3 + 1] = y;
      out[i * 3 + 2] = z;
      const m = Math.max(Math.abs(x), Math.abs(y), Math.abs(z));
      if (m > maxAbs) maxAbs = m;
    }
    const scale = maxAbs > 0 ? 1 / (maxAbs * NORMALIZE_PADDING) : 1;
    for (let i = 0; i < n * 3; i++) out[i] *= scale;
    return out;
  }, [cells, hasPcaData, axisX, axisY, axisZ]);

  // ---- three.js scene -------------------------------------------------------
  useEffect(() => {
    if (!mountRef.current || !positionBuffer || !colorBuffer) return;
    const mount = mountRef.current;
    const width = mount.clientWidth || 800;

    const scene = new THREE.Scene();
    scene.background = new THREE.Color(0x0e0e12);

    const camera = new THREE.PerspectiveCamera(60, width / height, 0.01, 100);
    camera.position.set(1.6, 1.2, 1.8);
    camera.lookAt(0, 0, 0);

    const renderer = new THREE.WebGLRenderer({ antialias: true });
    renderer.setSize(width, height);
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    mount.appendChild(renderer.domElement);

    // axes helper
    const axes = new THREE.AxesHelper(1);
    scene.add(axes);

    // point cloud
    const geom = new THREE.BufferGeometry();
    geom.setAttribute("position", new THREE.BufferAttribute(positionBuffer, 3));
    geom.setAttribute("color", new THREE.BufferAttribute(colorBuffer, 3));
    const mat = new THREE.PointsMaterial({
      size: POINT_SIZE,
      vertexColors: true,
      sizeAttenuation: true,
      transparent: true,
      opacity: 0.9,
    });
    const points = new THREE.Points(geom, mat);
    scene.add(points);

    const controls = new OrbitControls(camera, renderer.domElement);
    controls.enableDamping = true;
    controls.dampingFactor = 0.08;

    let rafId = 0;
    const animate = () => {
      controls.update();
      renderer.render(scene, camera);
      rafId = requestAnimationFrame(animate);
    };
    animate();

    const handleResize = () => {
      const w = mount.clientWidth || 800;
      camera.aspect = w / height;
      camera.updateProjectionMatrix();
      renderer.setSize(w, height);
    };
    window.addEventListener("resize", handleResize);

    return () => {
      window.removeEventListener("resize", handleResize);
      cancelAnimationFrame(rafId);
      controls.dispose();
      geom.dispose();
      mat.dispose();
      renderer.dispose();
      if (renderer.domElement.parentNode === mount) {
        mount.removeChild(renderer.domElement);
      }
    };
  }, [positionBuffer, colorBuffer, height]);

  // ---- render ---------------------------------------------------------------
  if (loadError) {
    return (
      <Box role="alert" sx={{ p: 3, color: "error.main", fontFamily: "monospace" }}>
        Failed to load PCA 3D: {loadError}
      </Box>
    );
  }
  if (!cells) {
    return (
      <Box
        sx={{
          height,
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          bgcolor: "#18181b",
          color: "text.secondary",
          borderRadius: 1,
        }}
      >
        Loading PCA…
      </Box>
    );
  }
  if (!hasPcaData) {
    return (
      <Box
        sx={{
          height,
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          bgcolor: "#18181b",
          color: "text.secondary",
          borderRadius: 1,
          fontFamily: "monospace",
        }}
      >
        PCA not computed for this dataset.
      </Box>
    );
  }

  const variance = dataset?.pc_variance ?? [];

  const axisLabel = (key: AxisKey): string => {
    const idx = Number(key.replace("pc", "")) - 1;
    const pct = variance[idx];
    return pct != null ? `${key.toUpperCase()} (${pct.toFixed(1)}%)` : key.toUpperCase();
  };

  return (
    <Box sx={{ display: "flex", flexDirection: "column", gap: 1 }}>
      <Box
        sx={{
          display: "flex",
          gap: 2,
          alignItems: "center",
          flexWrap: "wrap",
        }}
      >
        <Typography variant="caption" sx={{ color: "text.secondary" }}>
          X:
        </Typography>
        <AxisToggle value={axisX} onChange={setAxisX} labelKey="X" />
        <Typography variant="caption" sx={{ color: "text.secondary" }}>
          Y:
        </Typography>
        <AxisToggle value={axisY} onChange={setAxisY} labelKey="Y" />
        <Typography variant="caption" sx={{ color: "text.secondary" }}>
          Z:
        </Typography>
        <AxisToggle value={axisZ} onChange={setAxisZ} labelKey="Z" />
      </Box>
      <Box
        ref={mountRef}
        data-testid="expression-pca3d-canvas"
        sx={{
          height,
          borderRadius: 1,
          overflow: "hidden",
          border: "1px solid",
          borderColor: "divider",
        }}
      />
      <Typography
        variant="caption"
        sx={{ color: "text.secondary", fontFamily: "monospace" }}
      >
        {`${axisLabel(axisX)}  ·  ${axisLabel(axisY)}  ·  ${axisLabel(axisZ)}`}
      </Typography>
    </Box>
  );
}

function AxisToggle({
  value,
  onChange,
  labelKey,
}: {
  value: AxisKey;
  onChange: (v: AxisKey) => void;
  labelKey: string;
}) {
  return (
    <ToggleButtonGroup
      size="small"
      value={value}
      exclusive
      onChange={(_e, v) => {
        if (v) onChange(v as AxisKey);
      }}
      aria-label={`${labelKey} axis`}
    >
      {AXIS_KEYS.slice(0, AXIS_COUNT).map((k) => (
        <ToggleButton key={k} value={k} sx={{ py: 0.25, px: 1, fontSize: 11 }}>
          {k.toUpperCase()}
        </ToggleButton>
      ))}
    </ToggleButtonGroup>
  );
}

export default ExpressionPca3d;
