"use client";

import { useMemo } from "react";
import Box from "@mui/material/Box";
import Slider from "@mui/material/Slider";
import Typography from "@mui/material/Typography";

import { VIRIDIS_STOPS } from "@/components/expression-lib/viridis";

export interface ExpressionColorbarProps {
  /** Observed data min (typically 0 for log-normalized scRNA). */
  dataMin: number;
  /** Observed data max. */
  dataMax: number;
  /** Currently-applied color range (subset of [dataMin, dataMax]). */
  range: { min: number; max: number };
  /** Emitted when the user drags the slider handles. */
  onRangeChange: (range: { min: number; max: number }) => void;
  /** e.g. 'Seurat::NormalizeData logNormalize scale.factor=10000'. */
  unitsLabel: string;
  /** Gene whose expression this colorbar represents. */
  geneName: string;
  height?: number;
}

function viridisCssGradient(): string {
  const stops = VIRIDIS_STOPS.map(([r, g, b], i) => {
    const pct = (i / (VIRIDIS_STOPS.length - 1)) * 100;
    const rgb = `rgb(${Math.round(r * 255)}, ${Math.round(g * 255)}, ${Math.round(b * 255)})`;
    return `${rgb} ${pct.toFixed(2)}%`;
  });
  return `linear-gradient(to top, ${stops.join(", ")})`;
}

/**
 * Vertical viridis colorbar with a two-handle MUI Slider alongside it.
 * The values are log-normalized expression units (unmodified from the
 * source normalization); the caption surfaces `unitsLabel` so users
 * can read the scale literally rather than interpreting a [0, 1] ramp.
 */
export function ExpressionColorbar({
  dataMin,
  dataMax,
  range,
  onRangeChange,
  unitsLabel,
  geneName,
  height = 240,
}: ExpressionColorbarProps) {
  const gradient = useMemo(viridisCssGradient, []);
  const step = useMemo(() => {
    const span = dataMax - dataMin;
    if (span <= 0) return 0.01;
    return span / 100;
  }, [dataMin, dataMax]);

  const handleChange = (_e: Event, value: number | number[]) => {
    if (!Array.isArray(value) || value.length !== 2) return;
    onRangeChange({ min: value[0], max: value[1] });
  };

  return (
    <Box
      role="group"
      aria-label="Expression colorbar"
      data-testid="expression-colorbar"
      sx={{
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        gap: 1,
        width: 96,
      }}
    >
      <Typography variant="caption" sx={{ fontFamily: "monospace" }}>
        {geneName}
      </Typography>
      <Box sx={{ display: "flex", alignItems: "stretch", gap: 1, height }}>
        <Box
          sx={{
            width: 16,
            borderRadius: 0.5,
            background: gradient,
            border: "1px solid rgba(0,0,0,0.12)",
          }}
        />
        <Slider
          orientation="vertical"
          min={dataMin}
          max={dataMax}
          step={step}
          value={[range.min, range.max]}
          onChange={handleChange}
          valueLabelDisplay="auto"
          valueLabelFormat={(v) => v.toFixed(2)}
          sx={{ height }}
        />
      </Box>
      <Typography
        variant="caption"
        sx={{ fontFamily: "monospace", textAlign: "center", fontSize: 10 }}
      >
        {`${range.min.toFixed(2)} – ${range.max.toFixed(2)}`}
      </Typography>
      <Typography
        variant="caption"
        sx={{ fontSize: 10, textAlign: "center", color: "text.secondary" }}
      >
        {unitsLabel}
      </Typography>
    </Box>
  );
}

export default ExpressionColorbar;
