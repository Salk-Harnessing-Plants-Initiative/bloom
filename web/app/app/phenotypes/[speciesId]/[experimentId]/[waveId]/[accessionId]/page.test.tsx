// @vitest-environment jsdom
/**
 * The wave × accession page offers "Run this accession" over every scan of
 * every listed plant, and a checkbox beside each rendered thumbnail
 * (add-cyl-pipeline-ui task 11.5).
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import type { RunPipelineDialogProps } from "@/components/cyl-pipeline/RunPipelineDialog";

type Scan = { id: number; plant_age_days: number | null; cyl_images: { id: number }[] };
const db = vi.hoisted(() => ({ plants: [] as unknown[] }));
const dialog = vi.hoisted(() => ({ props: null as RunPipelineDialogProps | null }));

vi.mock("@/lib/supabase/server", () => {
  const rows: Record<string, () => unknown> = {
    cyl_experiments: () => ({ id: 5, name: "exp-five", species: { id: 2, common_name: "pennycress" }, people: null }),
    cyl_plants: () => db.plants,
  };
  const builder = (table: string) => {
    const b: Record<string, unknown> = {};
    for (const m of ["select", "eq", "order"]) b[m] = () => b;
    b.single = async () => ({ data: rows[table]?.() ?? null, error: null });
    b.then = (resolve: (v: unknown) => unknown) => resolve({ data: rows[table]?.() ?? null, error: null });
    return b;
  };
  return { createServerSupabaseClient: async () => ({ from: builder }), getUser: async () => null };
});
vi.mock("mixpanel", () => ({ default: { init: vi.fn() } }));
vi.mock("@/components/scientist-badge", () => ({ default: () => null }));
vi.mock("@/components/plant-scan", () => ({
  default: ({ scan, href }: { scan: { id: number }; href: string }) => <a href={href}>thumbnail {scan.id}</a>,
}));
vi.mock("@/components/cyl-pipeline/RunPipelineDialog", () => ({
  RunPipelineDialog: (props: RunPipelineDialogProps) => {
    dialog.props = props;
    return <div role="dialog" />;
  },
}));

import Accession from "./page";

function plant(id: number, qr: string, cyl_scans: Scan[]) {
  return {
    id,
    qr_code: qr,
    cyl_qc_codes: [],
    cyl_scans,
    accessions: { id: 7, name: "Col-0" },
    cyl_waves: { id: 11, number: 1 },
  };
}

beforeEach(() => {
  // Plant 1: two day-3 scans (only the first renders), and a day-7 scan with no frame-1 image.
  db.plants = [
    plant(1, "A", [
      { id: 12, plant_age_days: 5, cyl_images: [{ id: 120 }] },
      { id: 11, plant_age_days: 3, cyl_images: [{ id: 110 }] },
      { id: 14, plant_age_days: 3, cyl_images: [{ id: 140 }] },
      { id: 15, plant_age_days: 7, cyl_images: [] },
    ]),
    plant(2, "B", [{ id: 21, plant_age_days: 3, cyl_images: [{ id: 210 }] }]),
  ];
  dialog.props = null;
});
afterEach(() => cleanup());

const page = () => Accession({ params: Promise.resolve({ speciesId: "2", experimentId: "5", waveId: "11", accessionId: "7" }) });

describe("the wave × accession page", () => {
  it("runs every scan of every listed plant, in the plants' own order, before the page sorts them", async () => {
    render(await page());
    fireEvent.click(screen.getByRole("button", { name: "Run this accession" }));
    expect(dialog.props!.target).toEqual({ target_level: "scan_ids", scan_ids: [12, 11, 14, 15, 21] });
    expect(dialog.props!.title).toContain("Col-0");
  });

  it("puts a checkbox beside each rendered thumbnail, outside its link", async () => {
    render(await page());
    const boxes = screen.getAllByRole("checkbox") as HTMLInputElement[];
    expect(boxes).toHaveLength(3);
    for (const box of boxes) expect(box.closest("a")).toBeNull();
    expect(screen.getAllByRole("link", { name: /thumbnail/ }).map((a) => a.textContent)).toEqual(["thumbnail 11", "thumbnail 12", "thumbnail 21"]);
  });

  it("selects exactly the rendered scans with Select all shown, and runs them", async () => {
    render(await page());
    fireEvent.click(screen.getByRole("button", { name: "Select all shown" }));
    fireEvent.click(screen.getByRole("button", { name: "Run selected (3)" }));
    expect(dialog.props!.target).toEqual({ target_level: "scan_ids", scan_ids: [11, 12, 21] });
  });

  it("disables Run this accession over the limit", async () => {
    db.plants = [plant(1, "A", Array.from({ length: 5001 }, (_, i) => ({ id: i + 1, plant_age_days: null, cyl_images: [] })))];
    render(await page());
    expect((screen.getByRole("button", { name: "Run this accession" }) as HTMLButtonElement).disabled).toBe(true);
    expect(screen.getByText(/at most 5000/)).toBeTruthy();
  });
});
