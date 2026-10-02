// @vitest-environment jsdom
/** The drill-down's scan table (add-cyl-pipeline-ui task 7.3), rendered with the real DataGrid. */

import { afterEach, describe, expect, it } from "vitest";
import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { RunScansTable, type ScanTableRow } from "./RunScansTable";

afterEach(() => cleanup());

function row(scan_id: number, overrides: Partial<ScanTableRow> = {}): ScanTableRow {
  return {
    id: scan_id,
    scan_id,
    status: "queued",
    statusLabel: "Waiting",
    attempts: 0,
    error_message: null,
    argo_workflow_name: null,
    source_id: null,
    updated_at: "2026-09-28T10:00:01+00:00",
    qr_code: `QR-${scan_id}`,
    wave_number: 1,
    plant_age_days: 14,
    current: false,
    likelyCause: null,
    lateResultNote: null,
    scanHref: `/app/phenotypes/2/5/11/7/${scan_id}`,
    ...overrides,
  };
}

const bodyRows = () => screen.getAllByRole("row").filter((r) => within(r).queryAllByRole("gridcell").length > 0);

describe("RunScansTable", () => {
  // Rendering 100 auto-height DataGrid rows in jsdom takes about 2 s alone, more under a full parallel run.
  it("pages 5000 rows 100 at a time", { timeout: 20_000 }, () => {
    render(<RunScansTable rows={Array.from({ length: 5000 }, (_, i) => row(i + 1))} disableVirtualization />);
    expect(screen.getByText(/1–100 of 5000/)).toBeTruthy();
    expect(bodyRows()).toHaveLength(100);
  });

  it("shows every column in the spec's list", () => {
    render(<RunScansTable rows={[row(577)]} disableVirtualization />);
    const headers = screen.getAllByRole("columnheader").map((h) => h.textContent?.trim());
    expect(headers).toEqual([
      "Scan",
      "Plant QR code",
      "Wave",
      "Day",
      "Status",
      "Attempts",
      "Error",
      "Argo workflow",
      "Source",
      "Current in trait views",
      "Updated",
      "Images",
    ]);
  });

  it("fills each cell, with the raw status as secondary text and the failure hints under the error", () => {
    render(
      <RunScansTable
        rows={[
          row(577, {
            status: "failed",
            statusLabel: "Failed",
            attempts: 2,
            error_message: "stage-in failed",
            argo_workflow_name: "cyl-abc12",
            source_id: 40,
            current: true,
            likelyCause: "Likely cause: plant age missing",
            lateResultNote: "late result note",
          }),
        ]}
        disableVirtualization
      />,
    );
    const [r] = bodyRows();
    const cells = within(r).getAllByRole("gridcell").map((c) => c.textContent);
    expect(cells[0]).toBe("577");
    expect(cells[1]).toBe("QR-577");
    expect(cells[2]).toBe("1");
    expect(cells[3]).toBe("14");
    expect(cells[4]).toBe("Failedfailed");
    expect(cells[5]).toBe("2");
    expect(cells[6]).toContain("stage-in failed");
    expect(cells[6]).toContain("Likely cause: plant age missing");
    expect(cells[6]).toContain("late result note");
    expect(cells[7]).toBe("cyl-abc12");
    expect(cells[8]).toBe("40");
    expect(cells[9]).toBe("yes");
    expect(cells[10]).toContain("2026-09-28");
    expect(within(r).getByRole("link", { name: "Scan images" }).getAttribute("href")).toBe("/app/phenotypes/2/5/11/7/577");
  });

  it("says current is unknown when the latest source hasn't loaded, and gives no link without metadata", () => {
    render(<RunScansTable rows={[row(577, { current: null, scanHref: null })]} disableVirtualization />);
    const cells = within(bodyRows()[0]).getAllByRole("gridcell").map((c) => c.textContent);
    expect(cells[9]).toBe("unknown");
    expect(within(bodyRows()[0]).queryByRole("link")).toBeNull();
  });

  it("labels each status, with the raw value alongside", () => {
    const labels = [
      ["queued", "Waiting"],
      ["predicted", "Waiting"],
      ["written", "Result recorded"],
      ["reused", "Result recorded"],
      ["failed", "Failed"],
      ["mystery", "mystery"],
    ];
    render(
      <RunScansTable rows={labels.map(([status, statusLabel], i) => row(i + 1, { status, statusLabel }))} disableVirtualization />,
    );
    const statusCells = bodyRows().map((r) => within(r).getAllByRole("gridcell")[4].textContent);
    expect(statusCells).toEqual(labels.map(([status, label]) => `${label}${status}`));
  });

  it("filters by status", async () => {
    render(
      <RunScansTable
        rows={[
          row(1),
          row(2, { status: "written", statusLabel: "Result recorded" }),
          row(3, { status: "failed", statusLabel: "Failed" }),
          row(4, { status: "failed", statusLabel: "Failed" }),
          row(5, { status: "predicted", statusLabel: "Waiting" }),
        ]}
        disableVirtualization
      />,
    );
    const select = screen.getByLabelText("Show") as HTMLSelectElement;
    await act(async () => fireEvent.change(select, { target: { value: "failed" } }));
    expect(bodyRows().map((r) => within(r).getAllByRole("gridcell")[0].textContent)).toEqual(["3", "4"]);
    await act(async () => fireEvent.change(select, { target: { value: "waiting" } }));
    expect(bodyRows().map((r) => within(r).getAllByRole("gridcell")[0].textContent)).toEqual(["1", "5"]);
    await act(async () => fireEvent.change(select, { target: { value: "recorded" } }));
    expect(bodyRows().map((r) => within(r).getAllByRole("gridcell")[0].textContent)).toEqual(["2"]);
    await act(async () => fireEvent.change(select, { target: { value: "all" } }));
    expect(bodyRows()).toHaveLength(5);
  });

  it("opens on the requested filter, with counts per option", () => {
    render(
      <RunScansTable rows={[row(1), row(2, { status: "failed", statusLabel: "Failed" })]} initialFilter="failed" disableVirtualization />,
    );
    expect((screen.getByLabelText("Show") as HTMLSelectElement).value).toBe("failed");
    expect(bodyRows()).toHaveLength(1);
    expect(screen.getByRole("option", { name: "Failed (1)" })).toBeTruthy();
    expect(screen.getByRole("option", { name: "All (2)" })).toBeTruthy();
  });
});
