"use client";

import { useCallback, useEffect, useMemo, useState } from "react";

import { ExpressionSampleToggles } from "@/components/expression-sample-toggles";
import { IntegrationLegend } from "@/components/integration-legend";
import {
  IntegrationPointDetails,
  type PointMember,
} from "@/components/integration-point-details";
import { IntegrationUmap } from "@/components/integration-umap";
import { fetchJointArrays, fetchLabelCodes } from "@/components/integration-lib/joint-client";
import { labelAnchors } from "@/components/expression-lib/umap-labels";
import {
  topGroups,
  TRANSGENE_FACET,
  TRANSGENE_POSITIVE,
  transgeneBadge,
} from "@/components/expression-lib/transgene";
import { TransgeneSummary } from "@/components/transgene-summary";
import { TransgeneToggle } from "@/components/transgene-toggle";
import {
  CELL_TYPE_KEY,
  combinedRow,
  countFlagged,
  countFocused,
  countLevels,
  countNoValue,
  DATASET_KEY,
  datasetRow,
  describeFocus,
  focusIsChosen,
  GENOTYPE_KEY,
  normalisePositions,
  packColours,
  packFocus,
  packVisibility,
  palette,
  pointValues,
  rgbCss,
  type LabelRow,
} from "@/components/integration-lib/joint-map";

export interface IntegrationMember extends PointMember {
  ordinal: number;
  nPoints: number;
}

/** Rows with this few values also get a row of tags in the controls. */
const CHIP_ROW_MAX_LEVELS = 4;
const NOTHING: ReadonlySet<number> = new Set();

type Sets = Map<string, Set<number>>;

function toggled(prev: Sets, key: string, level: number): Sets {
  const next = new Map(prev);
  const values = new Set(next.get(key) ?? []);
  if (!values.delete(level)) values.add(level);
  next.set(key, values);
  return next;
}

const message = (err: unknown) => (err instanceof Error ? err.message : String(err));

const rowTitle = (key: string) =>
  key === DATASET_KEY ? "Datasets" : key === CELL_TYPE_KEY ? "Cell type" : key;

interface Props {
  embeddingId: number;
  members: IntegrationMember[];
  labelKeys: string[];
  /** Which label holds each dataset's cell types, as [member ordinal, label key]. */
  cellTypeLabels: [number, string][];
}

/** The joint map: every control in a section on top, the map across the whole
 *  width below it, and a clicked cell's details over the map's corner. */
export function IntegrationView({ embeddingId, members, labelKeys, cellTypeLabels }: Props) {
  const [base, setBase] = useState<{ positions: Float32Array; datasets: LabelRow } | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [labelRows, setLabelRows] = useState<ReadonlyMap<string, LabelRow>>(new Map());
  const [labelErrors, setLabelErrors] = useState<string[]>([]);
  const [colourBy, setColourBy] = useState(DATASET_KEY);
  const [hidden, setHidden] = useState<Sets>(new Map());
  const [focused, setFocused] = useState<Sets>(new Map());
  const [selected, setSelected] = useState<number | null>(null);
  const [fullScreen, setFullScreen] = useState(false);
  const [showTransgene, setShowTransgene] = useState(true);

  useEffect(() => {
    if (!fullScreen) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setFullScreen(false);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [fullScreen]);

  useEffect(() => {
    let cancelled = false;
    setBase(null);
    setLoadError(null);
    setLabelRows(new Map());
    setLabelErrors([]);
    fetchJointArrays(embeddingId).then(
      (arrays) => {
        if (cancelled) return;
        if (!arrays) {
          setLoadError("this map has no cells to show.");
          return;
        }
        setBase({
          positions: normalisePositions(arrays.x, arrays.y),
          datasets: datasetRow(members, arrays.memberOrdinals),
        });
      },
      (err) => {
        if (!cancelled) setLoadError(message(err));
      },
    );
    // Each label arrives on its own, so a slow one holds up none of the others.
    for (const key of labelKeys) {
      fetchLabelCodes(embeddingId, key).then(
        ({ levels, codes }) => {
          if (!cancelled) setLabelRows((prev) => new Map(prev).set(key, { key, levels, codes }));
        },
        (err) => {
          if (!cancelled) setLabelErrors((prev) => [...prev, message(err)]);
        },
      );
    }
    return () => {
      cancelled = true;
    };
  }, [embeddingId, members, labelKeys]);

  const n = base ? base.positions.length / 2 : 0;
  const orderedMembers = useMemo(
    () => [...members].sort((a, b) => a.ordinal - b.ordinal),
    [members],
  );

  // Ready once every dataset's cell-type label has arrived.
  const cellTypeRow = useMemo(() => {
    if (!base || cellTypeLabels.length === 0) return null;
    const sources = new Map<number, LabelRow>();
    for (const [ordinal, key] of cellTypeLabels) {
      const level = orderedMembers.findIndex((m) => m.ordinal === ordinal);
      if (level < 0) continue;
      const row = labelRows.get(key);
      if (!row || row.codes.length !== n) return null;
      sources.set(level, row);
    }
    return combinedRow(CELL_TYPE_KEY, base.datasets, sources);
  }, [base, cellTypeLabels, orderedMembers, labelRows, n]);

  const rows = useMemo<LabelRow[]>(() => {
    if (!base) return [];
    const labels = labelKeys
      .map((key) => labelRows.get(key))
      .filter((row): row is LabelRow => !!row && row.codes.length === n);
    return [base.datasets, ...(cellTypeRow ? [cellTypeRow] : []), ...labels];
  }, [base, cellTypeRow, labelKeys, labelRows, n]);

  const colourRow = rows.find((row) => row.key === colourBy) ?? rows[0] ?? null;
  const colourTriples = useMemo(() => palette(colourRow?.levels.length ?? 0), [colourRow]);
  const colours = useMemo(
    () => (colourRow ? packColours(colourRow, colourTriples) : null),
    [colourRow, colourTriples],
  );
  const visibility = useMemo(() => packVisibility(rows, hidden), [rows, hidden]);
  const focus = useMemo(() => packFocus(rows, focused), [rows, focused]);
  const counts = useMemo(() => countLevels(rows, hidden), [rows, hidden]);
  const noValue = useMemo(
    () => new Map(rows.map((row) => [row.key, countNoValue(row)])),
    [rows],
  );
  const focusSet = focusIsChosen(focused);
  const focusedCount = useMemo(
    () => (focusSet ? countFocused(rows, focused, hidden) : 0),
    [focusSet, rows, focused, hidden],
  );
  const shownCount = useMemo(() => visibility.reduce((sum, v) => sum + v, 0), [visibility]);
  const anyHidden = [...hidden.values()].some((values) => values.size > 0);

  // Transgene-positive cells, over every cell that records a transgene status:
  // in all, and per value of what the map is coloured by.
  const transgeneRow = labelRows.get(TRANSGENE_FACET);
  const positiveLevel = transgeneRow ? transgeneRow.levels.indexOf(TRANSGENE_POSITIVE) : -1;
  const transgeneTotals = useMemo(() => {
    if (!transgeneRow || positiveLevel < 0) return null;
    let positive = 0;
    let recorded = 0;
    for (let i = 0; i < transgeneRow.codes.length; i++) {
      const code = transgeneRow.codes[i];
      if (code < 0) continue;
      recorded++;
      if (code === positiveLevel) positive++;
    }
    return { positive, recorded };
  }, [transgeneRow, positiveLevel]);
  const flagged = useMemo(() => {
    if (!colourRow || !transgeneRow || positiveLevel < 0) return null;
    if (colourRow.key === TRANSGENE_FACET || transgeneRow.codes.length !== colourRow.codes.length) {
      return null;
    }
    return countFlagged(colourRow, transgeneRow, positiveLevel);
  }, [colourRow, transgeneRow, positiveLevel]);

  // Names on the map for what it is coloured by, each with a green badge for
  // its transgene-positive cells. Datasets are left unnamed: they lie over one
  // another, so no one spot belongs to any of them.
  const mapLabels = useMemo(() => {
    if (!base || !colourRow || colourRow.key === DATASET_KEY) return [];
    return labelAnchors(base.positions, colourRow.codes, colourRow.levels.length, visibility).map(
      (anchor) => ({
        text: colourRow.levels[anchor.level],
        x: anchor.x,
        y: anchor.y,
        badge:
          showTransgene && flagged ? transgeneBadge(flagged[anchor.level]) ?? undefined : undefined,
      }),
    );
  }, [base, colourRow, visibility, flagged, showTransgene]);

  const toggleHidden = useCallback(
    (key: string, level: number) => setHidden((prev) => toggled(prev, key, level)),
    [],
  );
  const toggleFocus = useCallback(
    (key: string, level: number) => setFocused((prev) => toggled(prev, key, level)),
    [],
  );
  const setHiddenLevels = useCallback(
    (key: string, levels: number[]) =>
      setHidden((prev) => new Map(prev).set(key, new Set(levels))),
    [],
  );

  // The hover label: what the map is coloured by, then the dataset, cell type
  // and genotype, whichever of those the colouring does not already say.
  const describe = useCallback(
    (i: number) => {
      if (!colourRow || !base) return { title: "", detail: "" };
      const valueOf = (row: LabelRow | null | undefined) =>
        row && row.codes[i] >= 0 ? row.levels[row.codes[i]] : null;
      const title = valueOf(colourRow) ?? `No ${rowTitle(colourRow.key).toLowerCase()}`;
      const detail = [
        colourRow.key === DATASET_KEY ? null : valueOf(base.datasets),
        colourRow.key === CELL_TYPE_KEY ? null : valueOf(cellTypeRow),
        valueOf(labelRows.get(GENOTYPE_KEY)),
      ]
        .filter(Boolean)
        .join(" · ");
      return { title, detail };
    },
    [colourRow, base, cellTypeRow, labelRows],
  );

  const fmt = new Intl.NumberFormat("en-US");
  const total = members.reduce((sum, m) => sum + m.nPoints, 0);

  if (loadError) {
    return (
      <div role="alert" className="rounded-lg border border-rose-200 bg-rose-50 p-4 text-sm text-rose-800">
        Could not load this map: {loadError}
      </div>
    );
  }

  const chipRows = rows.filter(
    (row) => row.key === DATASET_KEY || row.levels.length <= CHIP_ROW_MAX_LEVELS,
  );
  const selectedMember =
    selected !== null && base ? orderedMembers[base.datasets.codes[selected]] ?? null : null;
  const mainOptions = [
    { key: DATASET_KEY, label: "Dataset", ready: true },
    ...(cellTypeLabels.length > 0
      ? [{ key: CELL_TYPE_KEY, label: "Cell type", ready: cellTypeRow !== null }]
      : []),
  ];
  const isOtherLabel = colourBy !== DATASET_KEY && colourBy !== CELL_TYPE_KEY;

  return (
    <div
      className={`flex flex-col gap-3 ${
        fullScreen ? "fixed inset-0 z-50 overflow-auto bg-stone-100 p-4" : ""
      }`}
    >
      <section
        aria-label="Map controls"
        className="flex flex-col gap-3 rounded-lg border border-stone-200 bg-white p-4"
      >
        <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
          <span className="text-[10px] uppercase tracking-widest text-stone-500">Colour by</span>
          <div
            role="group"
            aria-label="Colour by"
            className="inline-flex rounded-md border border-stone-300 bg-white p-0.5"
          >
            {mainOptions.map((option) => (
              <button
                key={option.key}
                type="button"
                aria-pressed={colourBy === option.key}
                disabled={!option.ready}
                onClick={() => setColourBy(option.key)}
                className={`rounded px-3 py-1 text-sm transition-colors disabled:cursor-wait disabled:text-stone-400 ${
                  colourBy === option.key
                    ? "bg-stone-800 text-white"
                    : "text-stone-700 hover:bg-stone-100"
                }`}
              >
                {option.label}
                {option.ready ? "" : "…"}
              </button>
            ))}
          </div>
          <select
            aria-label="Colour by another label"
            value={isOtherLabel ? colourBy : ""}
            onChange={(e) => setColourBy(e.target.value)}
            className={`rounded-md border px-2 py-1 text-sm ${
              isOtherLabel
                ? "border-stone-800 bg-stone-800 text-white"
                : "border-stone-300 bg-white text-stone-700"
            }`}
          >
            <option value="" disabled>
              Another label…
            </option>
            {labelKeys.map((key) => (
              <option key={key} value={key} disabled={!labelRows.has(key)}>
                {key}
                {labelRows.has(key) ? "" : " (loading…)"}
              </option>
            ))}
          </select>
          <div className="ml-auto flex items-center gap-3">
            {base && (
              <span className="text-xs tabular-nums text-stone-500" role="status">
                Showing {fmt.format(shownCount)} of {fmt.format(n)} cells
              </span>
            )}
            <button
              type="button"
              onClick={() => setFullScreen((on) => !on)}
              aria-pressed={fullScreen}
              title={fullScreen ? "Back to the page (Esc)" : "Fill the window with the map"}
              className="rounded-md border border-stone-300 bg-white px-2.5 py-1 text-xs text-stone-700 hover:bg-stone-100"
            >
              {fullScreen ? "Exit full screen" : "Full screen"}
            </button>
          </div>
        </div>

        {transgeneTotals && (
          <div className="flex flex-wrap items-center gap-2">
            <TransgeneToggle on={showTransgene} onChange={setShowTransgene} />
            {showTransgene && transgeneTotals.positive > 0 && (
              <TransgeneSummary
                positive={transgeneTotals.positive}
                total={transgeneTotals.recorded}
                totalNoun="cells that record it"
                top={
                  flagged && colourRow
                    ? topGroups(colourRow.levels.map((name, i) => ({ name, positive: flagged[i] })))
                    : []
                }
              />
            )}
          </div>
        )}

        {colourRow && (
          <IntegrationLegend
            title={rowTitle(colourRow.key)}
            levels={colourRow.levels}
            colours={colourTriples.map(rgbCss)}
            counts={counts.get(colourRow.key) ?? []}
            noValueCount={noValue.get(colourRow.key) ?? 0}
            hidden={hidden.get(colourRow.key) ?? NOTHING}
            focused={focused.get(colourRow.key) ?? NOTHING}
            badges={showTransgene && flagged ? flagged.map(transgeneBadge) : undefined}
            onToggle={(level) => toggleHidden(colourRow.key, level)}
            onFocus={(level) => toggleFocus(colourRow.key, level)}
            onShowAll={() => setHiddenLevels(colourRow.key, [])}
            onHideAll={() => setHiddenLevels(colourRow.key, colourRow.levels.map((_, i) => i))}
          />
        )}

        {chipRows.length > 0 && (
          <div className="flex flex-col gap-1.5 border-t border-stone-100 pt-3">
            {chipRows.map((row) => {
              const rowCounts = counts.get(row.key) ?? [];
              const names = (set: ReadonlySet<number> | undefined) =>
                new Set([...(set ?? [])].map((i) => row.levels[i]));
              const indexOf = (name: string) => row.levels.indexOf(name);
              const isDatasets = row.key === DATASET_KEY;
              return (
                <ExpressionSampleToggles
                  key={row.key}
                  label={rowTitle(row.key)}
                  noun={isDatasets ? "dataset" : `${row.key} value`}
                  samples={row.levels.map((name, i) => ({ name, count: rowCounts[i] ?? 0 }))}
                  hidden={names(hidden.get(row.key))}
                  unlabelledCount={noValue.get(row.key) ?? 0}
                  onToggle={(name) => toggleHidden(row.key, indexOf(name))}
                  onShowAll={() => setHiddenLevels(row.key, [])}
                  focused={names(focused.get(row.key))}
                  onFocus={(name) => toggleFocus(row.key, indexOf(name))}
                />
              );
            })}
          </div>
        )}

        {(anyHidden || focusSet) && (
          <div className="flex flex-col gap-1 text-xs text-stone-500" role="status">
            {focusSet && (
              <span>
                {fmt.format(focusedCount)} {focusedCount === 1 ? "cell is" : "cells are"}{" "}
                {describeFocus(rows, focused)}; every other cell is greyed out.{" "}
                <button
                  type="button"
                  onClick={() => setFocused(new Map())}
                  className="underline hover:text-stone-700"
                >
                  Clear focus
                </button>
              </span>
            )}
            {anyHidden && (
              <span>
                Some values are hidden.{" "}
                <button
                  type="button"
                  onClick={() => setHidden(new Map())}
                  className="underline hover:text-stone-700"
                >
                  Show every cell
                </button>
              </span>
            )}
          </div>
        )}

        {labelErrors.length > 0 && (
          <div role="alert" className="text-xs text-rose-700">
            {labelErrors.join(" · ")}
          </div>
        )}
      </section>

      <section aria-label="Map" className="relative">
        {base && colours ? (
          <IntegrationUmap
            positions={base.positions}
            colours={colours}
            visibility={visibility}
            focus={focus}
            focusSet={focusSet}
            selected={selected}
            describe={describe}
            onPick={setSelected}
            fill={fullScreen}
            labels={mapLabels}
          />
        ) : (
          <div className="flex h-[70vh] min-h-[520px] items-center justify-center rounded-lg bg-zinc-900 text-sm text-zinc-400">
            Loading {fmt.format(total)} cells…
          </div>
        )}
        {selected !== null && base && (
          <div className="absolute right-3 top-3 z-10 max-h-[calc(100%-5rem)] w-72 overflow-y-auto rounded-lg shadow-lg">
            <IntegrationPointDetails
              embeddingId={embeddingId}
              index={selected}
              member={selectedMember}
              values={pointValues(selected, rows)}
              onClose={() => setSelected(null)}
            />
          </div>
        )}
      </section>
      <p className="text-[11px] text-stone-400">
        Zoom with the slider, the wheel or a pinch; drag to pan; click a cell for its details.
      </p>
    </div>
  );
}

export default IntegrationView;
