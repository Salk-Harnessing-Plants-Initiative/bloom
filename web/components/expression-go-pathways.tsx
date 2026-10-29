"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import Alert from "@mui/material/Alert";
import CircularProgress from "@mui/material/CircularProgress";
import FormControl from "@mui/material/FormControl";
import InputLabel from "@mui/material/InputLabel";
import MenuItem from "@mui/material/MenuItem";
import Paper from "@mui/material/Paper";
import Select from "@mui/material/Select";
import ToggleButton from "@mui/material/ToggleButton";
import ToggleButtonGroup from "@mui/material/ToggleButtonGroup";

import Link from "next/link";
import { createClientSupabaseClient } from "@/lib/supabase/client";
import { fetchComparisons } from "@/components/expression-differential-analysis";
import {
  clustersOf,
  fetchLatestEnrichment,
  fetchRunMakingEnrichment,
  geneRatio,
  goTermUrl,
  keggMapUrl,
  KIND_LABELS,
  KINDS,
  termsFor,
  type ClusterChoice,
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

// Each kind's terms, as the "nothing stood out" message names them.
const KIND_PLURAL: Record<TermKind, string> = {
  GO_BP: "biological process terms",
  GO_MF: "molecular function terms",
  GO_CC: "cellular component terms",
  KEGG: "KEGG pathways",
};

// What a list gene needs to count towards "List genes" for each kind.
const KIND_NOUN: Record<TermKind, string> = {
  GO_BP: "a biological process term",
  GO_MF: "a molecular function term",
  GO_CC: "a cellular component term",
  KEGG: "a KEGG pathway",
};

const SHORT_KIND: Record<TermKind, string> = {
  GO_BP: "Biological process",
  GO_MF: "Molecular function",
  GO_CC: "Cellular component",
  KEGG: "KEGG pathways",
};

type Loaded =
  | { state: "loading" }
  | { state: "none"; runId: number | null }
  | { state: "error"; message: string }
  | { state: "ready"; file: EnrichmentFile; clusters: ClusterChoice[] };

export interface ExpressionGoPathwaysProps {
  datasetId: number;
}

/** The dataset's GO and KEGG pathway enrichment, one comparison, direction and kind at a time. */
export function ExpressionGoPathways({ datasetId }: ExpressionGoPathwaysProps) {
  const [loaded, setLoaded] = useState<Loaded>({ state: "loading" });
  const [cluster, setCluster] = useState<string>("");
  const [comparison, setComparison] = useState<string>("");
  const [direction, setDirection] = useState<Direction>("up");
  const [kind, setKind] = useState<TermKind>("GO_BP");
  // The KEGG pathway whose map is open under the table, if any.
  const [mapOf, setMapOf] = useState<EnrichmentTerm | null>(null);

  useEffect(() => {
    let cancelled = false;
    setLoaded({ state: "loading" });
    const supabase = createClientSupabaseClient();
    fetchLatestEnrichment(supabase, datasetId)
      .then(async (found) => {
        if (!found) return { runId: await fetchRunMakingEnrichment(supabase, datasetId) };
        // Which cluster each comparison belongs to, from the analysis the file tested.
        const rows = await fetchComparisons(supabase, found.file.de_run_id);
        return { file: found.file, clusters: clustersOf(found.file, rows) };
      })
      .then((ready) => {
        if (cancelled) return;
        if ("runId" in ready) {
          setLoaded({ state: "none", runId: ready.runId ?? null });
          return;
        }
        setLoaded({ state: "ready", ...ready });
        const first = ready.clusters[0];
        setCluster(first?.cluster ?? "");
        setComparison(first?.comparisons[0]?.comparison ?? "");
      })
      .catch((error: Error) => {
        if (!cancelled) setLoaded({ state: "error", message: error.message });
      });
    return () => {
      cancelled = true;
    };
  }, [datasetId]);

  const file = loaded.state === "ready" ? loaded.file : null;
  const clusters = loaded.state === "ready" ? loaded.clusters : [];
  const choices = clusters.find((c) => c.cluster === cluster)?.comparisons ?? [];
  const chosen = choices.find((c) => c.comparison === comparison);
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
    return loaded.runId != null ? (
      <Alert severity="info">
        GO and pathway results are being made.{" "}
        <Link href={`/app/rnaseq-runs/${loaded.runId}`} className="underline">
          See the run
        </Link>
      </Alert>
    ) : (
      <Alert severity="info">
        No GO or pathway results for this dataset yet.
      </Alert>
    );
  }
  if (loaded.state === "error") {
    return <Alert severity="error">GO and pathway results couldn&apos;t be read: {loaded.message}</Alert>;
  }

  const { file: f } = loaded;
  // A run can be ready with nothing significant anywhere; then there's nothing to choose from.
  if (f.results.length === 0) {
    return <Alert severity="info">No GO term or pathway stood out in any comparison of this dataset.</Alert>;
  }
  return (
    <div className="space-y-4">
      <Paper sx={{ p: 2 }}>
        <div className="flex flex-wrap items-center gap-3">
          <FormControl sx={{ minWidth: 160 }} size="small">
            <InputLabel id="go-cluster-label">Cluster</InputLabel>
            <Select
              labelId="go-cluster-label"
              label="Cluster"
              value={cluster}
              onChange={(e) => {
                const next = clusters.find((c) => c.cluster === e.target.value);
                setCluster(e.target.value);
                setComparison(next?.comparisons[0]?.comparison ?? "");
              }}
            >
              {clusters.map((c) => (
                <MenuItem key={c.cluster} value={c.cluster}>{c.cluster}</MenuItem>
              ))}
            </Select>
          </FormControl>
          {/* Most clusters have one comparison, against all other cells; then there's nothing to choose. */}
          {choices.length > 1 && (
            <FormControl sx={{ minWidth: 200 }} size="small">
              <InputLabel id="go-comparison-label">Compared with</InputLabel>
              <Select
                labelId="go-comparison-label"
                label="Compared with"
                value={comparison}
                onChange={(e) => setComparison(e.target.value)}
              >
                {choices.map((c) => (
                  <MenuItem key={c.comparison} value={c.comparison}>{c.label}</MenuItem>
                ))}
              </Select>
            </FormControl>
          )}
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
        <p className="mt-2 text-sm text-stone-600">
          Genes {direction} in cluster {cluster} compared with {chosen?.label ?? "all other cells"},
          from the Differential expression tab.
        </p>
        <p className="mt-1 text-xs text-stone-500">
          {f.annotated_genes.toLocaleString("en-US")} of this dataset&apos;s{" "}
          {f.background_genes.toLocaleString("en-US")} genes have a GO term or KEGG pathway.
        </p>
        <details className="mt-1 text-xs text-stone-600">
          <summary className="cursor-pointer text-stone-500 hover:text-stone-800">
            How were these found?
          </summary>
          <ul className="mt-2 ml-4 list-disc space-y-1">
            <li>
              <b>Genes:</b> cluster {cluster} vs {chosen?.label ?? "all other cells"} (Differential
              expression tab), FDR &lt; {f.params.fdr_max}, top {f.params.top_n}.
            </li>
            <li><b>Test:</b> terms with more of these genes than chance would give.</li>
            <li>
              <b>Tested only:</b> terms carried by {f.params.min_term}–{f.params.max_term} of this
              dataset&apos;s genes.
            </li>
            <li><b>Shown:</b> term FDR &lt; {f.params.fdr_max}; smaller is stronger.</li>
            <li><b>Same genes:</b> only the most specific term is kept.</li>
            <li><b>Annotation:</b> {f.annotation}</li>
          </ul>
        </details>
      </Paper>

      {terms.length === 0 ? (
        <Alert severity="info">
          No {KIND_PLURAL[kind]} stood out for the genes {direction} in cluster {cluster}{" "}
          compared with {chosen?.label ?? "all other cells"}.
        </Alert>
      ) : (
        <>
          <Paper sx={{ p: 2 }}>
            <h3 className="font-serif italic text-stone-800 mb-2">
              {KIND_LABELS[kind]}: genes {direction} in cluster {cluster} vs{" "}
              {chosen?.label ?? "all other cells"}
            </h3>
            <DotPlot terms={terms.slice(0, DOT_PLOT_TERMS)} />
          </Paper>
          <Paper sx={{ p: 2 }}>
            <TermTable terms={terms} kind={kind} onShowMap={setMapOf} />
          </Paper>
          {mapOf && terms.includes(mapOf) && (
            <PathwayMap term={mapOf} onClose={() => setMapOf(null)} />
          )}
        </>
      )}

    </div>
  );
}

const ROW = 22;
const LEFT = 300;
const PLOT_WIDTH = 360;
const TOP = 8;
const BOTTOM = 36;
const RIGHT = 120;
// The legend's height, so a plot of one or two terms still shows all of it.
const LEGEND_HEIGHT = 100;

function DotPlot({ terms }: { terms: EnrichmentTerm[] }) {
  const maxRatio = Math.max(...terms.map(geneRatio)) * 1.1 || 1;
  const counts = terms.map((t) => t.list_genes_in_term);
  const [minCount, maxCount] = [Math.min(...counts), Math.max(...counts)];
  const scores = terms.map((t) => -Math.log10(Math.max(t.fdr, Number.MIN_VALUE)));
  const [lo, hi] = [Math.min(...scores), Math.max(...scores)];
  const height = Math.max(TOP + terms.length * ROW + BOTTOM, LEGEND_HEIGHT);
  const bottom = height - BOTTOM;
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
          <line x1={x(t)} x2={x(t)} y1={TOP} y2={bottom} stroke="#e7e5e4" />
          <text x={x(t)} y={bottom + 14} fontSize={10} textAnchor="middle" fill="#57534e">
            {shareLabel(t)}
          </text>
        </g>
      ))}
      <text x={LEFT + PLOT_WIDTH / 2} y={height - 6} fontSize={11} textAnchor="middle" fill="#44403c">
        Share of list genes in this term
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
        {lo !== hi && (
          <>
            <rect y={20} width={10} height={10} fill={colour(lo)} />
            <text x={14} y={29}>{lo.toFixed(1)}</text>
          </>
        )}
        <text y={50}>Genes in list</text>
        <circle cx={7} cy={64} r={radius(maxCount)} fill="none" stroke="#44403c" />
        <text x={22} y={67}>{maxCount}</text>
        {minCount !== maxCount && (
          <>
            <circle cx={7} cy={84} r={radius(minCount)} fill="none" stroke="#44403c" />
            <text x={22} y={87}>{minCount}</text>
          </>
        )}
      </g>
    </svg>
  );
}

function TermTable({ terms, kind, onShowMap }: {
  terms: EnrichmentTerm[];
  kind: TermKind;
  onShowMap: (term: EnrichmentTerm) => void;
}) {
  return (
    <div className="max-h-[480px] overflow-auto">
      <table aria-label="Enriched terms" className="w-full text-sm">
        <thead className="sticky top-0 bg-white text-left text-stone-600">
          <tr>
            <th className="py-2 pr-4 font-medium">Term</th>
            <th className="py-2 pr-4 font-medium whitespace-nowrap"
                title={`Of the list genes that have ${KIND_NOUN[kind]}`}>List genes</th>
            <th className="py-2 pr-4 font-medium whitespace-nowrap"
                title="In this dataset">Genes with term</th>
            <th className="py-2 pr-4 font-medium">FDR</th>
            <th className="py-2 font-medium">Genes</th>
          </tr>
        </thead>
        <tbody>
          {terms.map((t) => (
            <tr key={t.term} className="border-t border-stone-100 align-top">
              <td className="py-1.5 pr-4">
                <span className="text-stone-900">{t.name}</span>{" "}
                {goTermUrl(t) ? (
                  <a href={goTermUrl(t) ?? undefined} target="_blank" rel="noopener noreferrer"
                     title="Open this term on QuickGO"
                     className="text-xs text-lime-800 underline hover:text-lime-900">
                    {t.term} ↗
                  </a>
                ) : (
                  <span className="text-xs text-stone-500">{t.term}</span>
                )}
                {keggMapUrl(t) && (
                  <button
                    type="button"
                    onClick={() => onShowMap(t)}
                    className="ml-2 whitespace-nowrap text-xs text-lime-800 underline hover:text-lime-900"
                  >
                    Show map
                  </button>
                )}
              </td>
              <td className="py-1.5 pr-4 tabular-nums whitespace-nowrap">{t.list_genes_in_term} of {t.list_size}</td>
              <td className="py-1.5 pr-4 tabular-nums whitespace-nowrap">{t.term_size}</td>
              <td className="py-1.5 pr-4 tabular-nums whitespace-nowrap">{t.fdr.toExponential(2)}</td>
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

// KEGG's map page is laid out this wide and tall, then scaled down to fit the panel, so the
// whole map shows without scrolling sideways.
const MAP_PAGE_WIDTH = 1400;
const MAP_PAGE_HEIGHT = 1000;

/** KEGG's own map of a pathway, the list genes coloured, shown in the page. */
function PathwayMap({ term, onClose }: { term: EnrichmentTerm; onClose: () => void }) {
  const url = keggMapUrl(term);
  const box = useRef<HTMLDivElement>(null);
  const [scale, setScale] = useState(1);
  useEffect(() => {
    const el = box.current;
    if (!el || typeof ResizeObserver === "undefined") return;
    const fit = () => setScale(Math.min(1, el.clientWidth / MAP_PAGE_WIDTH));
    fit();
    const observer = new ResizeObserver(fit);
    observer.observe(el);
    return () => observer.disconnect();
  }, []);
  if (!url) return null;
  return (
    <Paper sx={{ p: 2 }}>
      <div className="mb-2 flex items-center gap-3">
        <h3 className="font-serif italic text-stone-800">
          {term.name}: {term.list_genes_in_term} list genes {term.direction}, in{" "}
          {term.direction === "up" ? "red" : "blue"}
        </h3>
        <a href={url} target="_blank" rel="noopener noreferrer"
           className="text-xs text-lime-800 underline hover:text-lime-900">
          Open on KEGG ↗
        </a>
        <button type="button" onClick={onClose} aria-label="Close map"
                className="ml-auto text-sm text-stone-500 hover:text-stone-800">
          ✕
        </button>
      </div>
      <p className="mb-2 text-xs text-stone-500">
        A coloured box has at least one of these genes. Shrunk to fit; open on KEGG for full size.
      </p>
      <div ref={box} className="w-full overflow-hidden rounded border border-stone-200"
           style={{ height: MAP_PAGE_HEIGHT * scale }}>
        <iframe
          title={`KEGG map: ${term.name}`}
          src={url}
          style={{ width: MAP_PAGE_WIDTH, height: MAP_PAGE_HEIGHT, border: 0,
                   transform: `scale(${scale})`, transformOrigin: "top left" }}
          referrerPolicy="no-referrer"
          sandbox="allow-scripts allow-same-origin allow-popups"
        />
      </div>
    </Paper>
  );
}

/** An axis tick as a percentage to two significant figures, so close ticks don't repeat. */
export function shareLabel(share: number): string {
  return share === 0 ? "0%" : `${Number((share * 100).toPrecision(2))}%`;
}

export default ExpressionGoPathways;
