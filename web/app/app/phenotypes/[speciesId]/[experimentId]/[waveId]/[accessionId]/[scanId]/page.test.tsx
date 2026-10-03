// @vitest-environment jsdom
/** The scan page offers "Run this scan" only when the scan exists (add-cyl-pipeline-ui task 11.5). */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";

const db = vi.hoisted(() => ({ scan: null as Record<string, unknown> | null }));

vi.mock("@/lib/supabase/server", () => {
  const rows: Record<string, () => unknown> = {
    cyl_experiments: () => ({ id: 5, name: "exp-five", species: { id: 2, common_name: "pennycress" }, people: null }),
    accessions: () => ({ id: 7, name: "Col-0" }),
    cyl_scans: () => db.scan,
  };
  const builder = (table: string) => {
    const b: Record<string, unknown> = {};
    for (const m of ["select", "eq", "order"]) b[m] = () => b;
    b.single = async () => ({ data: rows[table]?.() ?? null, error: null });
    return b;
  };
  return { createServerSupabaseClient: async () => ({ from: builder }), getUser: async () => null };
});
vi.mock("@/lib/supabase/scan-video", () => ({ getStoredScanVideoUrl: async () => null }));
vi.mock("mixpanel", () => ({ default: { init: vi.fn() } }));
vi.mock("@/components/scientist-badge", () => ({ default: () => null }));
vi.mock("@/components/plant-image", () => ({ default: () => null }));
vi.mock("@/components/scan-frame-viewer", () => ({ default: () => null }));
vi.mock("@/components/scan-video-button", () => ({ default: () => null }));
vi.mock("@/components/cyl-pipeline/RunPipelineButton", () => ({
  RunPipelineButton: (props: { label: string; target: unknown }) => (
    <button data-target={JSON.stringify(props.target)}>{props.label}</button>
  ),
}));

vi.mock("./ScanTraitExportButton", () => ({
  default: (props: { scanId: number }) => <button data-scan={props.scanId}>Download traits</button>,
}));

import ScanPage from "./page";

beforeEach(() => vi.stubEnv("CYL_PIPELINE_TRIGGER_ENABLED", "true"));
afterEach(() => {
  cleanup();
  db.scan = null;
  vi.unstubAllEnvs();
});

const page = () =>
  ScanPage({ params: Promise.resolve({ speciesId: "2", experimentId: "5", waveId: "11", accessionId: "7", scanId: "577" }) });

describe("the scan page", () => {
  it("offers Run this scan for an existing scan", async () => {
    db.scan = { id: 577, plant_age_days: 14, cyl_images: [], cyl_plants: { qr_code: "Q", cyl_waves: { number: 1 } } };
    render(await page());
    const run = screen.getByRole("button", { name: "Run this scan" });
    expect(JSON.parse(run.dataset.target!)).toEqual({ target_level: "scan", target_id: 577 });
  });

  it("offers nothing when the scan doesn't exist", async () => {
    render(await page());
    expect(screen.queryByRole("button", { name: "Run this scan" })).toBeNull();
  });

  it("offers nothing when starting runs is switched off", async () => {
    vi.stubEnv("CYL_PIPELINE_TRIGGER_ENABLED", "false");
    db.scan = { id: 577, plant_age_days: 14, cyl_images: [], cyl_plants: { qr_code: "Q", cyl_waves: { number: 1 } } };
    render(await page());
    expect(screen.getByText(/Replicate/)).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Run this scan" })).toBeNull();
  });

  it("offers Download traits for an existing scan whether or not runs can start", async () => {
    db.scan = { id: 577, plant_age_days: 14, cyl_images: [], cyl_plants: { qr_code: "Q", cyl_waves: { number: 1 } } };
    render(await page());
    expect(screen.getByRole("button", { name: "Download traits" }).dataset.scan).toBe("577");
    cleanup();
    vi.stubEnv("CYL_PIPELINE_TRIGGER_ENABLED", "false");
    render(await page());
    expect(screen.getByRole("button", { name: "Download traits" }).dataset.scan).toBe("577");
  });

  it("offers no Download traits when the scan doesn't exist", async () => {
    render(await page());
    expect(screen.queryByRole("button", { name: "Download traits" })).toBeNull();
  });
});
