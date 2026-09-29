"use client";

/**
 * The drill-down's per-scan table. `@mui/x-data-grid` is a webpack external
 * (next.config.js), hence a client component. Rows arrive already derived by
 * RunDetailLive; this only filters, pages and renders them.
 */

import Link from "next/link";
import { useId, useMemo, useState } from "react";
import { DataGrid, type GridColDef } from "@mui/x-data-grid";

export type StatusFilter = "all" | "waiting" | "recorded" | "failed";

/** One drill-down table row: the run-scan row plus what the view derived for it. */
export interface ScanTableRow {
  id: number;
  scan_id: number;
  status: string;
  /** The status label (Waiting, Result recorded, Failed, or the raw value). */
  statusLabel: string;
  attempts: number;
  error_message: string | null;
  argo_workflow_name: string | null;
  source_id: number | null;
  updated_at: string;
  qr_code: string | null;
  wave_number: number | null;
  plant_age_days: number | null;
  /** True/false when the scan's latest source is known; null when it isn't. */
  current: boolean | null;
  likelyCause: string | null;
  noOpNote: string | null;
  scanHref: string | null;
}

const MATCH: Record<Exclude<StatusFilter, "all">, (status: string) => boolean> = {
  waiting: (s) => s === "queued" || s === "predicted",
  recorded: (s) => s === "written" || s === "reused",
  failed: (s) => s === "failed",
};

const FILTER_LABELS: [StatusFilter, string][] = [
  ["all", "All"],
  ["waiting", "Waiting"],
  ["recorded", "Result recorded"],
  ["failed", "Failed"],
];

const columns: GridColDef<ScanTableRow>[] = [
  { field: "scan_id", headerName: "Scan", width: 90, type: "number", align: "left", headerAlign: "left" },
  { field: "qr_code", headerName: "Plant QR code", width: 140 },
  { field: "wave_number", headerName: "Wave", width: 70, type: "number", align: "left", headerAlign: "left" },
  { field: "plant_age_days", headerName: "Day", width: 70, type: "number", align: "left", headerAlign: "left" },
  {
    field: "statusLabel",
    headerName: "Status",
    width: 140,
    renderCell: ({ row }) => (
      <span className="flex flex-col py-1 leading-tight">
        <span>{row.statusLabel}</span>
        <span className="text-xs text-stone-500">{row.status}</span>
      </span>
    ),
  },
  { field: "attempts", headerName: "Attempts", width: 90, type: "number", align: "left", headerAlign: "left" },
  {
    field: "error_message",
    headerName: "Error",
    flex: 1,
    minWidth: 240,
    renderCell: ({ row }) => (
      <span className="flex flex-col gap-1 whitespace-normal break-words py-1 leading-tight">
        {row.error_message && <span className="max-h-24 overflow-auto">{row.error_message}</span>}
        {row.likelyCause && <span className="text-amber-700">{row.likelyCause}</span>}
        {row.noOpNote && <span className="text-stone-600">{row.noOpNote}</span>}
      </span>
    ),
  },
  { field: "argo_workflow_name", headerName: "Argo workflow", width: 150 },
  { field: "source_id", headerName: "Source", width: 90, type: "number", align: "left", headerAlign: "left" },
  {
    field: "current",
    headerName: "Current in trait views",
    width: 170,
    valueFormatter: (value: boolean | null) => (value === null ? "unknown" : value ? "yes" : "no"),
  },
  {
    field: "updated_at",
    headerName: "Updated",
    width: 190,
    // The raw UTC value: sortable text, and the same on server and client.
    valueFormatter: (value: string) => `${value.replace("T", " ").slice(0, 19)} UTC`,
  },
  {
    field: "scanHref",
    headerName: "Images",
    width: 120,
    sortable: false,
    renderCell: ({ row }) =>
      row.scanHref ? (
        <Link href={row.scanHref} className="text-lime-700 hover:underline">
          Scan images
        </Link>
      ) : null,
  },
];

export function RunScansTable({
  rows,
  initialFilter = "all",
  disableVirtualization = false,
}: {
  rows: ScanTableRow[];
  initialFilter?: StatusFilter;
  /** Tests render every row of a page; the app keeps virtualization. */
  disableVirtualization?: boolean;
}) {
  const filterId = useId();
  const [filter, setFilter] = useState<StatusFilter>(initialFilter);
  const counts = useMemo(() => {
    const c: Record<StatusFilter, number> = { all: rows.length, waiting: 0, recorded: 0, failed: 0 };
    for (const r of rows) {
      for (const key of ["waiting", "recorded", "failed"] as const) if (MATCH[key](r.status)) c[key] += 1;
    }
    return c;
  }, [rows]);
  const shown = useMemo(() => (filter === "all" ? rows : rows.filter((r) => MATCH[filter](r.status))), [rows, filter]);

  return (
    <div>
      <div className="mb-2 inline-flex items-center gap-2 text-sm text-stone-700">
        <label htmlFor={filterId}>Show</label>
        <select
          id={filterId}
          value={filter}
          onChange={(e) => setFilter(e.target.value as StatusFilter)}
          className="rounded-md border border-stone-300 bg-white px-2 py-1"
        >
          {FILTER_LABELS.map(([value, label]) => (
            <option key={value} value={value}>
              {label} ({counts[value]})
            </option>
          ))}
        </select>
      </div>
      <DataGrid
        rows={shown}
        columns={columns}
        initialState={{ pagination: { paginationModel: { pageSize: 100, page: 0 } } }}
        pageSizeOptions={[100]}
        getRowHeight={() => "auto"}
        disableRowSelectionOnClick
        disableVirtualization={disableVirtualization}
        autoHeight
        density="compact"
      />
    </div>
  );
}
