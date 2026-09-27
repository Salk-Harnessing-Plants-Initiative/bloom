import * as React from "react";
import { useEffect, useMemo, useState } from "react";
import Accordion from "@mui/material/Accordion";
import AccordionDetails from "@mui/material/AccordionDetails";
import AccordionSummary from "@mui/material/AccordionSummary";
import Alert from "@mui/material/Alert";
import Box from "@mui/material/Box";
import CircularProgress from "@mui/material/CircularProgress";
import FormControl from "@mui/material/FormControl";
import InputLabel from "@mui/material/InputLabel";
import MenuItem from "@mui/material/MenuItem";
import Select from "@mui/material/Select";
import Tab from "@mui/material/Tab";
import Tabs from "@mui/material/Tabs";
import Typography from "@mui/material/Typography";
import ExpandMoreIcon from "@mui/icons-material/ExpandMore";

import { createClientSupabaseClient } from "@/lib/supabase/client";
import { CellTypeGrid, GenesTable, ResultsTable, type OnSelect } from "./expression-de-summary-tables";
import {
  SUMMARY_MAX_RESULTS,
  comparisonLabel,
  countByCellType,
  fetchPassingResults,
  listResults,
  summaryTotals,
  tallyGenes,
  type PassingResult,
} from "./expression-lib/de-summary";
import { groupNames, type Cuts, type DeEntry } from "./expression-lib/de-types";

/** How long the cuts must stay still before the summary is read again. */
const CUTS_SETTLE_MS = 300;
const ALL_CONTRASTS = "__all__";

type Loaded = { results: PassingResult[]; truncated: boolean };

const plural = (n: number, word: string) => `${n.toLocaleString()} ${word}${n === 1 ? "" : "s"}`;

/** Every cell type of the analysis at once, at the tab's cuts. */
export default function DeSummary({ comparisons, cuts, onSelect }: {
  /** The analysis's comparisons, as the panel read them. */
  comparisons: DeEntry[];
  cuts: Cuts;
  onSelect: OnSelect;
}) {
  // Only comparisons within a cell type can be counted across cell types.
  const scoped = useMemo(() => comparisons.filter((entry) => entry.cluster_id), [comparisons]);
  const [expanded, setExpanded] = useState(false);
  const [tab, setTab] = useState(0);
  const [picked, setPicked] = useState<string | null>(null);
  const [loaded, setLoaded] = useState<Loaded | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const cutsValid = Number.isFinite(cuts.fdr) && Number.isFinite(cuts.log2fc);

  useEffect(() => {
    setPicked(null);
    setTab(0);
  }, [scoped]);

  useEffect(() => {
    if (!cutsValid) return;
    let cancelled = false;
    setLoading(true);
    const timer = setTimeout(() => {
      fetchPassingResults(createClientSupabaseClient(), scoped, { fdr: cuts.fdr, log2fc: cuts.log2fc })
        .then((out) => {
          if (cancelled) return;
          setLoaded(out);
          setError(null);
          setLoading(false);
        })
        .catch((err: unknown) => {
          if (cancelled) return;
          setLoaded(null);
          setError(err instanceof Error ? err.message : String(err));
          setLoading(false);
        });
    }, CUTS_SETTLE_MS);
    // A slower answer for earlier cuts must not land over the current one.
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [scoped, cuts.fdr, cuts.log2fc, cutsValid]);

  const contrasts = useMemo(() => {
    const seen = new Map<string, string>();
    for (const entry of scoped) {
      if (entry.contrast && !seen.has(entry.contrast)) seen.set(entry.contrast, comparisonLabel(entry));
    }
    return [...seen].map(([contrast, label]) => ({ contrast, label }));
  }, [scoped]);

  // With a single contrast there is nothing to pick, and every result shares its groups.
  const selection = contrasts.length === 1 ? contrasts[0].contrast : picked;
  const places = useMemo(
    () => (loaded && !loaded.truncated ? listResults(loaded.results, scoped, selection) : []),
    [loaded, scoped, selection],
  );
  const tally = useMemo(() => tallyGenes(places, scoped, selection), [places, scoped, selection]);
  const totals = useMemo(() => summaryTotals(places, scoped, selection), [places, scoped, selection]);
  const grid = useMemo(() => countByCellType(places, scoped, selection), [places, scoped, selection]);

  if (scoped.length === 0) return null;

  // Red and blue name a direction only where every result shares the same two groups.
  const coloured = selection !== null || contrasts.length === 0;
  const showContrast = contrasts.length > 1 && selection === null;
  const label = contrasts.find((c) => c.contrast === selection)?.label;
  const scope = label ? ` for ${label}` : "";
  const cutsText = `FDR < ${cuts.fdr}, |log2FC| > ${cuts.log2fc}`;
  const firstPicked = scoped.find((entry) => entry.contrast === selection);
  const groups = selection !== null && firstPicked ? groupNames(firstPicked) : null;

  let headline: string;
  if (!cutsValid) headline = "Enter a number for both cuts to summarise";
  else if (error) headline = "The summary could not be loaded";
  else if (loading || !loaded) headline = `Reading the results (${cutsText})…`;
  else if (loaded.truncated) {
    headline = `More than ${SUMMARY_MAX_RESULTS.toLocaleString()} results pass these cuts (${cutsText})`;
  } else if (totals.comparisonsTested === 0) {
    headline = `No comparison${scope || " in this analysis"} was tested`;
  } else if (totals.genes === 0) {
    headline = `No gene passes these cuts in any cell type${scope} (${cutsText})`;
  } else {
    headline = `${plural(totals.genes, "gene")} ${totals.genes === 1 ? "passes" : "pass"} in ` +
      `${totals.comparisonsWithResults} of ${plural(totals.comparisonsTested, "tested comparison")}` +
      `${scope} (${cutsText})`;
  }

  let body: React.ReactNode;
  if (!cutsValid) {
    body = <Typography variant="body2">Enter a number for both cuts above.</Typography>;
  } else if (error) {
    body = (
      <Alert severity="error">
        The summary could not be loaded: {error}. The comparison below is not affected.
      </Alert>
    );
  } else if (loading || !loaded) {
    body = <Box display="flex" justifyContent="center" p={2}><CircularProgress size={24} /></Box>;
  } else if (loaded.truncated) {
    body = (
      <Alert severity="warning">
        More than {SUMMARY_MAX_RESULTS.toLocaleString()} results pass {cutsText}. A summary of
        only some of them would miscount, so none is shown; tighten the cuts above.
      </Alert>
    );
  } else {
    body = (
      <>
        {contrasts.length > 1 && (
          <FormControl size="small" sx={{ minWidth: 240, mb: 1.5 }}>
            <InputLabel id="de-summary-contrast-label">Contrast</InputLabel>
            <Select
              labelId="de-summary-contrast-label"
              label="Contrast"
              value={picked ?? ALL_CONTRASTS}
              onChange={(e) => setPicked(e.target.value === ALL_CONTRASTS ? null : e.target.value)}
            >
              <MenuItem value={ALL_CONTRASTS}>All contrasts</MenuItem>
              {contrasts.map((c) => (
                <MenuItem key={c.contrast} value={c.contrast}>{c.label}</MenuItem>
              ))}
            </Select>
          </FormControl>
        )}
        <Typography variant="caption" color="text.secondary" display="block" mb={1.5}>
          A comparison is one cell type under one contrast. FDR is adjusted within each
          comparison, not across them, so counting the cell types a gene passes in points to
          genes worth opening; it is not a combined test, and larger cell types have more power
          to show a change. Click a result to open its comparison below.
        </Typography>
        {totals.comparisonsTested === 0 || totals.genes === 0 ? (
          <Typography variant="body2">{headline}.</Typography>
        ) : (
          <>
            <Tabs value={tab} onChange={(_, next: number) => setTab(next)} sx={{ mb: 1 }}>
              <Tab label="Genes" />
              <Tab label="All results" />
              <Tab label="By cell type" />
            </Tabs>
            {tab === 0 && (
              <GenesTable tally={tally} groups={groups} showContrast={showContrast}
                          coloured={coloured} onSelect={onSelect} />
            )}
            {tab === 1 && (
              <ResultsTable places={places} showContrast={showContrast}
                            coloured={coloured} onSelect={onSelect} />
            )}
            {tab === 2 && <CellTypeGrid grid={grid} coloured={coloured} onSelect={onSelect} />}
          </>
        )}
      </>
    );
  }

  return (
    <Accordion
      expanded={expanded}
      onChange={(_, open) => setExpanded(open)}
      disableGutters
      variant="outlined"
      sx={{ mb: 3 }}
      slotProps={{ transition: { unmountOnExit: true } }}
    >
      <AccordionSummary expandIcon={<ExpandMoreIcon />}>
        <Box display="flex" alignItems="center" gap={1.5} flexWrap="wrap">
          <Typography variant="subtitle1" fontWeight="bold">All cell type summary</Typography>
          <Typography variant="body2" color="text.secondary">{headline}</Typography>
          {loading && cutsValid && !error && <CircularProgress size={16} />}
        </Box>
      </AccordionSummary>
      <AccordionDetails>{body}</AccordionDetails>
    </Accordion>
  );
}
