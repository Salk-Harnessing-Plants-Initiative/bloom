"use client";

/**
 * A checkbox selection of the scans a page renders, run as one `scan_ids`
 * target (spec: "Run actions are offered…", wave × accession page; design
 * D7). Keyed by scan id, so two thumbnails of one scan are one selection.
 * Checkboxes sit beside the thumbnail's link, never inside it, so selecting
 * never navigates. The selection lives on the page only.
 */

import { createContext, useContext, useMemo, useState, type ReactNode } from "react";
import { plural } from "@/lib/cyl-pipeline/run-text";
import { RunPipelineButton } from "./RunPipelineButton";

interface Selection {
  selected: ReadonlySet<number>;
  toggle: (scanId: number) => void;
  selectAll: () => void;
  clear: () => void;
}

const SelectionContext = createContext<Selection | null>(null);

function useSelection(): Selection {
  const selection = useContext(SelectionContext);
  if (!selection) throw new Error("scan selection components need a ScanSelectionProvider");
  return selection;
}

export function ScanSelectionProvider({ shownIds, children }: { shownIds: number[]; children: ReactNode }) {
  const [selected, setSelected] = useState<ReadonlySet<number>>(() => new Set());
  const value = useMemo<Selection>(
    () => ({
      selected,
      toggle: (scanId) =>
        setSelected((prev) => {
          const next = new Set(prev);
          if (!next.delete(scanId)) next.add(scanId);
          return next;
        }),
      selectAll: () => setSelected((prev) => new Set([...prev, ...shownIds])),
      clear: () => setSelected(new Set()),
    }),
    [selected, shownIds],
  );
  return <SelectionContext.Provider value={value}>{children}</SelectionContext.Provider>;
}

export function ScanCheckbox({ scanId, label }: { scanId: number; label: string }) {
  const { selected, toggle } = useSelection();
  return (
    <input
      type="checkbox"
      aria-label={label}
      checked={selected.has(scanId)}
      onChange={() => toggle(scanId)}
      className="h-4 w-4 accent-lime-700"
    />
  );
}

export function SelectAllShown() {
  const { selectAll } = useSelection();
  return (
    <button type="button" onClick={selectAll} className="text-sm text-lime-700 underline hover:no-underline">
      Select all shown
    </button>
  );
}

export function SelectionBar() {
  const { selected, clear } = useSelection();
  if (selected.size === 0) return null;
  const ids = [...selected];
  return (
    <div
      data-testid="selection-bar"
      className="sticky bottom-4 z-10 mt-4 flex flex-wrap items-start gap-4 rounded-md border border-stone-300 bg-white p-3 text-sm shadow-md"
    >
      <span className="py-1">{ids.length} selected</span>
      <RunPipelineButton target={{ target_level: "scan_ids", scan_ids: ids }} label={`Run selected (${ids.length})`} title={plural(ids.length, "selected scan")} />
      <button type="button" onClick={clear} className="py-1 text-stone-600 underline hover:no-underline">
        Clear selection
      </button>
      <span className="py-1 text-xs text-stone-500">Selections are kept on this page only.</span>
    </div>
  );
}
