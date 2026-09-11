"use client";

import { useMemo } from "react";

import {
  colourScale,
  groupStats,
  type CellGroup,
} from "@/components/expression-lib/gene-stats";
import { viridis, VIRIDIS_STOPS } from "@/components/expression-lib/viridis";

const LABEL_WIDTH = 136;
const HEADER_HEIGHT = 150;
const CELL = 26;
const MAX_RADIUS = CELL / 2 - 2;
/** A dot for a group with no cell expressing, so its tooltip can still be read. */
const EMPTY_RADIUS = 1.5;

export function colourCss(t: number): string {
  const [r, g, b] = viridis(t);
  return `rgb(${Math.round(r * 255)}, ${Math.round(g * 255)}, ${Math.round(b * 255)})`;
}

const GRADIENT = `linear-gradient(to right, ${VIRIDIS_STOPS.map(
  ([r, g, b], i) =>
    `rgb(${Math.round(r * 255)}, ${Math.round(g * 255)}, ${Math.round(b * 255)}) ${(
      (i / (VIRIDIS_STOPS.length - 1)) *
      100
    ).toFixed(1)}%`,
).join(", ")})`;

export const percent = (share: number) =>
  `${(share * 100).toFixed(share > 0 && share < 0.1 ? 1 : 0)}%`;

const cellsWord = (n: number) => `${n.toLocaleString()} ${n === 1 ? "cell" : "cells"}`;

interface Props {
  genes: string[];
  groups: CellGroup[];
  values: ReadonlyMap<string, Float32Array>;
  perGene: boolean;
  chosen: string | null;
  onChoose: (gene: string) => void;
  unitsLabel: string;
}

/** Genes down, cell types across. A dot's size is the share of the cell type's
 *  cells expressing the gene; its colour the mean over all of them, zeros
 *  included, scaled per gene or on one scale. */
export function ExpressionGeneDotplot({
  genes,
  groups,
  values,
  perGene,
  chosen,
  onChoose,
  unitsLabel,
}: Props) {
  const rows = useMemo(() => genes.filter((g) => values.has(g)), [genes, values]);
  const stats = useMemo(
    () => rows.map((gene) => groups.map((group) => groupStats(values.get(gene)!, group.cells))),
    [rows, groups, values],
  );
  const { t, max } = useMemo(
    () => colourScale(stats.map((row) => row.map((s) => s?.mean ?? 0)), perGene),
    [stats, perGene],
  );

  if (rows.length === 0 || groups.length === 0) return null;
  const width = LABEL_WIDTH + groups.length * CELL + 16;
  const height = HEADER_HEIGHT + rows.length * CELL + 8;
  const columnX = (c: number) => LABEL_WIDTH + c * CELL + CELL / 2;

  return (
    <figure data-testid="gene-dotplot" className="m-0 flex flex-col gap-3">
      <div className="overflow-x-auto">
        <svg
          width={width}
          height={height}
          role="img"
          aria-label={`Dot plot of ${rows.length} genes across ${groups.length} columns`}
          className="text-stone-700"
        >
          {groups.map((group, c) => (
            <g key={group.key} data-testid="dotplot-column">
              <rect
                x={columnX(c) - CELL / 2 + 4}
                y={HEADER_HEIGHT - 8}
                width={CELL - 8}
                height={4}
                rx={2}
                fill={group.color ?? "#a8a29e"}
              />
              <text
                transform={`translate(${columnX(c) + 3}, ${HEADER_HEIGHT - 14}) rotate(-55)`}
                fontSize={11}
                fill="currentColor"
              >
                {group.part ? `${group.cellType} · ${group.part}` : group.cellType}
              </text>
            </g>
          ))}
          {rows.map((gene, r) => {
            const y = HEADER_HEIGHT + r * CELL + CELL / 2;
            return (
              <g key={gene} data-testid="dotplot-row">
                {gene === chosen && (
                  <rect x={0} y={y - CELL / 2} width={width} height={CELL} fill="#ecfccb" />
                )}
                <g
                  role="button"
                  tabIndex={0}
                  aria-label={`Show ${gene} by cell type`}
                  onClick={() => onChoose(gene)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" || e.key === " ") onChoose(gene);
                  }}
                  className="cursor-pointer"
                >
                  <text
                    x={LABEL_WIDTH - 10}
                    y={y + 4}
                    textAnchor="end"
                    fontSize={12}
                    fontFamily="ui-monospace, monospace"
                    fontWeight={gene === chosen ? 700 : 400}
                    fill="currentColor"
                  >
                    {gene}
                  </text>
                </g>
                {groups.map((group, c) => {
                  const s = stats[r][c];
                  if (!s) return null;
                  const expressed = s.expressing > 0;
                  return (
                    <circle
                      key={group.key}
                      data-testid="dotplot-dot"
                      cx={columnX(c)}
                      cy={y}
                      r={expressed ? Math.max(EMPTY_RADIUS, Math.sqrt(s.share) * MAX_RADIUS) : EMPTY_RADIUS}
                      fill={expressed ? colourCss(t[r][c]) : "none"}
                      stroke="rgba(0,0,0,0.2)"
                    >
                      <title>
                        {`${gene} in ${group.key}: ${cellsWord(s.n)}, ${s.expressing.toLocaleString()} expressing (${percent(s.share)}), mean ${s.mean.toFixed(3)}`}
                      </title>
                    </circle>
                  );
                })}
              </g>
            );
          })}
        </svg>
      </div>
      <figcaption className="flex flex-wrap items-center gap-x-8 gap-y-2 text-xs text-stone-600">
        <span className="flex items-center gap-2">
          Share of the cell type&apos;s cells expressing
          {[0.25, 0.5, 1].map((share) => (
            <span key={share} className="flex items-center gap-0.5">
              <svg width={CELL} height={CELL} aria-hidden>
                <circle cx={CELL / 2} cy={CELL / 2} r={Math.sqrt(share) * MAX_RADIUS} fill="#a8a29e" />
              </svg>
              {percent(share)}
            </span>
          ))}
        </span>
        <span className="flex items-center gap-2">
          Mean expression, zeros included ({unitsLabel})
          <span className="inline-block h-3 w-28 rounded-sm" style={{ background: GRADIENT }} />
          {perGene
            ? "0 → each gene's highest cell-type mean"
            : `0 → ${(max[0] ?? 0).toFixed(2)} for every gene`}
        </span>
      </figcaption>
    </figure>
  );
}

export default ExpressionGeneDotplot;
