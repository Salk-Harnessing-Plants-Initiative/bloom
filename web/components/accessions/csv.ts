// Minimal, dependency-free CSV helpers for the accession comparison exports.

type Cell = string | number | boolean | null | undefined;

// RFC-4180-ish: quote a field if it contains a comma, quote, or newline;
// escape embedded quotes by doubling them.
function escapeCell(value: Cell): string {
  const s = value == null ? "" : String(value);
  return /[",\r\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
}

export function toCsv(headers: string[], rows: Cell[][]): string {
  return [headers, ...rows]
    .map((row) => row.map(escapeCell).join(","))
    .join("\r\n");
}

// Trigger a client-side download of `csv` as `filename`. No-op on the server.
export function downloadCsv(filename: string, csv: string): void {
  if (typeof document === "undefined") return;
  const blob = new Blob(["﻿", csv], { type: "text/csv;charset=utf-8;" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}
