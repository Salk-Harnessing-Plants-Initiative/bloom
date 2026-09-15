"use client";

import { useMemo } from "react";

import { percent } from "@/components/expression-gene-dotplot";
import { groupStats, violinShape, type CellGroup } from "@/components/expression-lib/gene-stats";

const COLUMN = 64;
const PLOT_HEIGHT = 220;
const AXIS_WIDTH = 48;
const TOP = 10;
const LABEL_HEIGHT = 160;
const HALF_WIDTH = COLUMN / 2 - 6;
const BOX_WIDTH = 10;

interface Props {
  gene: string;
  groups: CellGroup[];
  values: Float32Array;
  unitsLabel: string;
}

/** One gene in each group: a violin of all its cells' values, zeros included,
 *  with the box, median and whiskers inside, on one value axis. */
export function ExpressionGeneViolin({ gene, groups, values, unitsLabel }: Props) {
  const { drawn, low, high } = useMemo(() => {
    const stats = groups.map((group) => groupStats(values, group.cells));
    const low = Math.min(0, ...stats.map((s) => s?.min ?? 0));
    const high = Math.max(low + 1e-6, ...stats.map((s) => s?.max ?? 0));
    const drawn = groups.flatMap((group, i) => {
      const s = stats[i];
      return s ? [{ group, stats: s, shape: violinShape(values, group.cells, [low, high]) }] : [];
    });
    return { drawn, low, high };
  }, [groups, values]);

  const y = (v: number) => TOP + PLOT_HEIGHT - ((v - low) / (high - low)) * PLOT_HEIGHT;
  const width = AXIS_WIDTH + drawn.length * COLUMN + 12;
  const ticks = [low, (low + high) / 2, high];

  return (
    <div className="overflow-x-auto">
      <svg
        width={width}
        height={TOP + PLOT_HEIGHT + LABEL_HEIGHT}
        role="img"
        aria-label={`${gene} in each of ${drawn.length} groups`}
        className="text-stone-700"
      >
        <line x1={AXIS_WIDTH} x2={AXIS_WIDTH} y1={TOP} y2={TOP + PLOT_HEIGHT} stroke="#a8a29e" />
        {ticks.map((tick) => (
          <g key={tick}>
            <line x1={AXIS_WIDTH - 4} x2={width} y1={y(tick)} y2={y(tick)} stroke="#e7e5e4" />
            <text x={AXIS_WIDTH - 6} y={y(tick) + 4} textAnchor="end" fontSize={10} fill="currentColor">
              {tick.toFixed(1)}
            </text>
          </g>
        ))}
        <text
          transform={`translate(12, ${TOP + PLOT_HEIGHT / 2}) rotate(-90)`}
          textAnchor="middle"
          fontSize={10}
          fill="currentColor"
        >
          {unitsLabel}
        </text>
        {drawn.map(({ group, stats: s, shape }, i) => {
          const cx = AXIS_WIDTH + i * COLUMN + COLUMN / 2;
          const colour = group.color ?? "#a8a29e";
          const outline = shape
            ? [
                ...shape.map((p) => `${cx - p.density * HALF_WIDTH},${y(p.value)}`),
                ...[...shape].reverse().map((p) => `${cx + p.density * HALF_WIDTH},${y(p.value)}`),
              ]
            : [];
          return (
            <g key={group.key} data-testid="violin-group">
              <title>
                {`${group.key}: ${s.n.toLocaleString()} cells, ${s.expressing.toLocaleString()} expressing (${percent(s.share)}), median ${s.median.toFixed(2)}, mean ${s.mean.toFixed(2)}`}
              </title>
              {shape && (
                <path
                  data-testid="violin-shape"
                  d={`M${outline.join("L")}Z`}
                  fill={colour}
                  fillOpacity={0.35}
                  stroke={colour}
                />
              )}
              <line x1={cx} x2={cx} y1={y(s.lowWhisker)} y2={y(s.highWhisker)} stroke="#57534e" />
              <rect
                x={cx - BOX_WIDTH / 2}
                y={y(s.q3)}
                width={BOX_WIDTH}
                height={Math.max(1, y(s.q1) - y(s.q3))}
                fill="#ffffff"
                stroke="#292524"
              />
              <line
                x1={cx - BOX_WIDTH / 2}
                x2={cx + BOX_WIDTH / 2}
                y1={y(s.median)}
                y2={y(s.median)}
                stroke="#292524"
                strokeWidth={2}
              />
              <text x={cx} y={TOP + PLOT_HEIGHT + 14} textAnchor="middle" fontSize={10} fill="currentColor">
                {`n ${s.n.toLocaleString()}`}
              </text>
              <text x={cx} y={TOP + PLOT_HEIGHT + 26} textAnchor="middle" fontSize={10} fill="#65a30d">
                {`${percent(s.share)} expr.`}
              </text>
              <text
                transform={`translate(${cx + 3}, ${TOP + PLOT_HEIGHT + 38}) rotate(55)`}
                fontSize={11}
                fill="currentColor"
              >
                {group.part ? `${group.cellType} · ${group.part}` : group.cellType}
              </text>
            </g>
          );
        })}
      </svg>
    </div>
  );
}

export default ExpressionGeneViolin;
