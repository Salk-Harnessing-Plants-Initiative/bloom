"use client";

import { useEffect, useMemo, useState } from "react";
import Alert from "@mui/material/Alert";
import CircularProgress from "@mui/material/CircularProgress";
import FormControl from "@mui/material/FormControl";
import InputLabel from "@mui/material/InputLabel";
import MenuItem from "@mui/material/MenuItem";
import Paper from "@mui/material/Paper";
import Select from "@mui/material/Select";
import ToggleButton from "@mui/material/ToggleButton";
import ToggleButtonGroup from "@mui/material/ToggleButtonGroup";

import { createClientSupabaseClient } from "@/lib/supabase/client";
import {
  comparisonsOf,
  fetchLatestEnrichment,
  geneRatio,
  KIND_LABELS,
  KINDS,
  termsFor,
  type Direction,
  type EnrichmentFile,
  type EnrichmentTerm,
  type TermKind,
} from "@/components/expression-lib/enrichment";
import { viridis } from "@/components/expression-lib/viridis";

// The dot plot shows this many of the most significant terms; the table shows them all.
const DOT_PLOT_TERMS = 20;
// Term names past this many characters are cut in the dot plot (the full name is its tooltip).
const LABEL_CHARS = 46;
// How many of a term's list genes the table shows before "+N more".
const SHOWN_GENES = 8;

const SHORT_KIND: Record<TermKind, string> = {
  GO_BP: "Biological process",
  GO_MF: "Molecular function",
  GO_CC: "Cellular component",
  KEGG: "KEGG pathways",
};

type Loaded =
  | { state: "loading" }
  | { state: "none" }
  | { state: "error"; message: string }
  | { state: "ready"; file: EnrichmentFile };

export interface ExpressionGoPathwaysProps {
  datasetId: number;
}

/** The dataset's GO and KEGG pathway enrichment, one comparison, direction and kind at a time. */
export function ExpressionGoPathways({ datasetId }: ExpressionGoPathwaysProps) {
  const [loaded, setLoaded] = useState<Loaded>({ state: "loading" });
  const [comparison, setComparison] = useState<string>("");
  const [direction, setDirection] = useState<Direction>("up");
  const [kind, setKind] = useState<TermKind>("GO_BP");

  useEffect(() => {
    let cancelled = false;
    setLoaded({ state: "loading" });
    fetchLatestEnrichment(createClientSupabaseClient(), datasetId)
      .then((found) => {
        if (cancelled) return;
        if (!found) {
          setLoaded({ state: "none" });
          return;
        }
        setLoaded({ state: "ready", file: found.file });
        setComparison(comparisonsOf(found.file)[0] ?? "");
      })
      .catch((error: Error) => {
        if (!cancelled) setLoaded({ state: "error", message: error.message });
      });
    return () => {
      cancelled = true;
    };
  }, [datasetId]);

  const file = loaded.state === "ready" ? loaded.file : null;
  const comparisons = useMemo(() => (file ? comparisonsOf(file) : []), [file]);
  const terms = useMemo(
    () => (file && comparison ? termsFor(file, comparison, direction, kind) : []),
    [file, comparison, direction, kind],
  );

  if (loaded.state === "loading") {
    return (
      <div className="flex items-center gap-3 p-6 text-sm text-stone-600">
        <CircularProgress size={20} /> Reading GO and pathway results…
      </div>
    );
  }
  if (loaded.state === "none") {
    return (
      <Alert severity="info">
        No GO and pathway results for this dataset yet. They are added by an enrichment run
        against an annotation of the dataset&apos;s genome.
      </Alert>
    );
  }
  if (loaded.state === "error") {
    return <Alert severity="error">GO and pathway results couldn&apos;t be read: {loaded.message}</Alert>;
  }

  const { file: f } = loaded;
  return (
    <div className="space-y-4">
      <Paper sx={{ p: 2 }}>
        <div className="flex flex-wrap items-center gap-3">
          <FormControl sx={{ minWidth: 220 }} size="small">
            <InputLabel id="go-comparison-label">Comparison</InputLabel>
            <Select
              labelId="go-comparison-label"
              label="Comparison"
              value={comparison}
              onChange={(e) => setComparison(e.target.value)}
            >
              {comparisons.map((c) => (
                <MenuItem key={c} value={c}>{c.replaceAll("_", " ")}</MenuItem>
              ))}
            </Select>
          </FormControl>
          <ToggleButtonGroup
            size="small"
            exclusive
            value={direction}
            onChange={(_, next: Direction | null) => next && setDirection(next)}
          >
            <ToggleButton value="up">Up</ToggleButton>
            <ToggleButton value="down">Down</ToggleButton>
          </ToggleButtonGroup>
          <ToggleButtonGroup
            size="small"
            exclusive
            value={kind}
            onChange={(_, next: TermKind | null) => next && setKind(next)}
          >
            {KINDS.map((k) => (
              <ToggleButton key={k} value={k}>{SHORT_KIND[k]}</ToggleButton>
            ))}
          </ToggleButtonGroup>
        </div>
      </Paper>

      {terms.length === 0 ? (
        <Alert severity="info">
          No significant {SHORT_KIND[kind].toLowerCase()} terms for {comparison.replaceAll("_", " ")}{" "}
          ({direction === "up" ? "up" : "down"}-regulated genes).
        </Alert>
      ) : (
        <>
          <Paper sx={{ p: 2 }}>
            <h3 className="font-serif italic text-stone-800 mb-2">
              {KIND_LABELS[kind]}, {direction === "up" ? "up" : "down"}-regulated in{" "}
              {comparison.replaceAll("_", " ")}
            </h3>
            <DotPlot terms={terms.slice(0, DOT_PLOT_TERMS)} />
          </Paper>
          <Paper sx={{ p: 2 }}>
            <TermTable terms={terms} />
          </Paper>
        </>
      )}

      <p className="text-xs text-stone-500">
        Annotation {f.annotation}; {f.annotated_genes.toLocaleString("en-US")} of{" "}
        {f.background_genes.toLocaleString("en-US")} genes in the dataset have a term. Lists:
        genes with FDR &lt; {f.params.fdr_max} and |log2FC| &gt; {f.params.lfc_min}, the{" "}
        {f.params.top_n} strongest; each kind is tested against the dataset&apos;s genes with a
        term of that kind (hypergeometric test, Benjamini–Hochberg). Terms covering the same
        genes show as the most specific one.
      </p>
    </div>
  );
}

const ROW = 22;
const LEFT = 300;
const PLOT_WIDTH = 360;
const TOP = 8;
const BOTTOM = 36;
const RIGHT = 120;

function DotPlot({ terms }: { terms: EnrichmentTerm[] }) {
  const maxRatio = Math.max(...terms.map(geneRatio)) * 1.1 || 1;
  const counts = terms.map((t) => t.list_genes_in_term);
  const [minCount, maxCount] = [Math.min(...counts), Math.max(...counts)];
  const scores = terms.map((t) => -Math.log10(Math.max(t.fdr, Number.MIN_VALUE)));
  const [lo, hi] = [Math.min(...scores), Math.max(...scores)];
  const height = TOP + terms.length * ROW + BOTTOM;
  const x = (ratio: number) => LEFT + (ratio / maxRatio) * PLOT_WIDTH;
  const radius = (n: number) =>
    maxCount === minCount ? 7 : 4 + ((n - minCount) / (maxCount - minCount)) * 7;
  const colour = (score: number) => {
    const [r, g, b] = viridis(hi === lo ? 1 : (score - lo) / (hi - lo));
    return `rgb(${Math.round(r * 255)},${Math.round(g * 255)},${Math.round(b * 255)})`;
  };
  const ticks = [0, 0.25, 0.5, 0.75, 1].map((f) => f * maxRatio);

  return (
    <svg
      role="img"
      aria-label="Dot plot of the most significant terms"
      viewBox={`0 0 ${LEFT + PLOT_WIDTH + RIGHT} ${height}`}
      className="w-full max-w-4xl"
    >
      {ticks.map((t) => (
        <g key={t}>
          <line x1={x(t)} x2={x(t)} y1={TOP} y2={height - BOTTOM} stroke="#e7e5e4" />
          <text x={x(t)} y={height - BOTTOM + 14} fontSize={10} textAnchor="middle" fill="#57534e">
            {t.toFixed(2)}
          </text>
        </g>
      ))}
      <text x={LEFT + PLOT_WIDTH / 2} y={height - 6} fontSize={11} textAnchor="middle" fill="#44403c">
        Gene ratio (list genes in the term / list size)
      </text>
      {terms.map((t, i) => {
        const y = TOP + i * ROW + ROW / 2;
        const label = t.name.length > LABEL_CHARS ? `${t.name.slice(0, LABEL_CHARS - 1)}…` : t.name;
        return (
          <g key={t.term}>
            <title>{`${t.name} (${t.term}): ${t.list_genes_in_term} of ${t.list_size} genes, FDR ${t.fdr.toExponential(2)}`}</title>
            <text x={LEFT - 8} y={y + 4} fontSize={11} textAnchor="end" fill="#292524">{label}</text>
            <circle cx={x(geneRatio(t))} cy={y} r={radius(t.list_genes_in_term)}
                    fill={colour(scores[i])} stroke="#1c1917" strokeWidth={0.5} />
          </g>
        );
      })}
      <g transform={`translate(${LEFT + PLOT_WIDTH + 24}, ${TOP + 4})`} fontSize={10} fill="#44403c">
        <text y={0}>−log10 FDR</text>
        <rect y={6} width={10} height={10} fill={colour(hi)} />
        <text x={14} y={15}>{hi.toFixed(1)}</text>
        <rect y={20} width={10} height={10} fill={colour(lo)} />
        <text x={14} y={29}>{lo.toFixed(1)}</text>
        <text y={50}>Genes in list</text>
        <circle cx={5} cy={62} r={radius(maxCount)} fill="none" stroke="#44403c" />
        <text x={18} y={65}>{maxCount}</text>
        <circle cx={5} cy={82} r={radius(minCount)} fill="none" stroke="#44403c" />
        <text x={18} y={85}>{minCount}</text>
      </g>
    </svg>
  );
}

function TermTable({ terms }: { terms: EnrichmentTerm[] }) {
  return (
    <div className="max-h-[480px] overflow-auto">
      <table aria-label="Enriched terms" className="w-full text-sm">
        <thead className="sticky top-0 bg-white text-left text-stone-600">
          <tr>
            <th className="py-2 pr-4 font-medium">Term</th>
            <th className="py-2 pr-4 font-medium">In list</th>
            <th className="py-2 pr-4 font-medium">Term size</th>
            <th className="py-2 pr-4 font-medium">FDR</th>
            <th className="py-2 font-medium">List genes</th>
          </tr>
        </thead>
        <tbody>
          {terms.map((t) => (
            <tr key={t.term} className="border-t border-stone-100 align-top">
              <td className="py-1.5 pr-4">
                <span className="text-stone-900">{t.name}</span>{" "}
                <span className="text-xs text-stone-500">{t.term}</span>
              </td>
              <td className="py-1.5 pr-4 tabular-nums">{t.list_genes_in_term} / {t.list_size}</td>
              <td className="py-1.5 pr-4 tabular-nums">{t.term_size}</td>
              <td className="py-1.5 pr-4 tabular-nums">{t.fdr.toExponential(2)}</td>
              <td className="py-1.5 text-xs text-stone-600" title={t.genes.join(", ")}>
                {t.genes.slice(0, SHOWN_GENES).join(", ")}
                {t.genes.length > SHOWN_GENES && ` +${t.genes.length - SHOWN_GENES} more`}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export default ExpressionGoPathways;
