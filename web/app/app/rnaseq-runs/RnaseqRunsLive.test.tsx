// @vitest-environment jsdom
/** The RNA-seq runs list: what each row shows, filters, and live changes. */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";

vi.mock("@/lib/cyl-pipeline/use-live-sync", async () => ({
  useLiveSync: (await import("./live-sync-mock")).useLiveSyncMock,
}));
vi.mock("@/lib/supabase/client", () => ({ createClientSupabaseClient: () => ({}) }));

import RnaseqRunsLive from "./RnaseqRunsLive";
import { emit, refresh } from "./live-sync-mock";
import type { RnaseqRun } from "@/lib/rnaseq-runs";

function run(id: number, overrides: Partial<RnaseqRun> = {}): RnaseqRun {
  return {
    id,
    workflow_type: "scrna-cellranger",
    status: "running",
    current_step: "count",
    params: { sample: `sample${id}`, reference: "tiny_ref" },
    metadata: { species_id: 1, dataset_name: `Dataset ${id}` },
    requested_by: id === 1 ? "me" : "4965b3af-ccfe-40f5-814b-447e1f726e1b",
    argo_workflow_name: null,
    step_pods: null,
    exit_code: null,
    message: null,
    created_at: "2026-09-30T00:03:00Z",
    submitted_at: null,
    completed_at: null,
    genome_version_id: null,
    ...overrides,
  };
}

function renderList(runs = [run(2, { status: "succeeded" }), run(1)]) {
  render(
    <RnaseqRunsLive
      initialRuns={runs}
      speciesLabels={{ 1: "Arabidopsis (Arabidopsis thaliana)" }}
      currentUserId="me"
    />
  );
}

function rows() {
  return screen.queryAllByRole("link").map((a) => a.textContent ?? "");
}

beforeEach(() => refresh.mockClear());
afterEach(cleanup);

describe("RnaseqRunsLive", () => {
  it("shows each run's inputs, dataset, status, step and who started it, newest first", () => {
    renderList();
    const links = screen.getAllByRole("link");
    expect(links.map((a) => a.getAttribute("href"))).toEqual([
      "/app/rnaseq-runs/2",
      "/app/rnaseq-runs/1",
    ]);
    const running = within(links[1]);
    expect(running.getByText("sample1 · tiny_ref")).toBeTruthy();
    expect(running.getByText("Dataset 1 · Arabidopsis (Arabidopsis thaliana)")).toBeTruthy();
    expect(running.getByText("Running")).toBeTruthy();
    expect(running.getByText("Cell Ranger count")).toBeTruthy();
    // Who started a run is "you" or another member's short id, never a name or email.
    expect(running.getByText("you")).toBeTruthy();
    expect(within(links[0]).getByText("another member · 4965b3af")).toBeTruthy();
    expect(screen.queryByText(/@/)).toBeNull();
    // A finished run shows no step.
    expect(within(links[0]).queryByText("Cell Ranger count")).toBeNull();
  });

  it("filters by status", () => {
    renderList();
    fireEvent.change(screen.getByLabelText(/Show/), { target: { value: "active" } });
    expect(rows()).toHaveLength(1);
    expect(rows()[0]).toContain("sample1");
    fireEvent.change(screen.getByLabelText(/Show/), { target: { value: "succeeded" } });
    expect(rows()[0]).toContain("sample2");
    fireEvent.change(screen.getByLabelText(/Show/), { target: { value: "failed" } });
    expect(screen.getByText("No runs match this filter.")).toBeTruthy();
  });

  it("shows only the user's own runs, and refreshes, when Only mine is ticked", () => {
    renderList();
    fireEvent.click(screen.getByLabelText("Only mine"));
    expect(rows()).toHaveLength(1);
    expect(rows()[0]).toContain("sample1");
    expect(refresh).toHaveBeenCalled();
  });

  it("moves a run to its new step without a reload", () => {
    renderList();
    emit({ id: 1, status: "running", current_step: "cleanup" });
    expect(within(screen.getAllByRole("link")[1]).getByText("Clean up")).toBeTruthy();
  });

  it("adds a newly started run at the top, with who started it", () => {
    renderList();
    emit(run(3, { status: "queued", current_step: null }), "INSERT");
    const first = screen.getAllByRole("link")[0];
    expect(first.getAttribute("href")).toBe("/app/rnaseq-runs/3");
    expect(within(first).getByText("another member · 4965b3af")).toBeTruthy();
  });

  it("points to the Expression page when there are no runs", () => {
    renderList([]);
    expect(screen.getByText("No RNA-seq runs yet. Start one from the Expression page.")).toBeTruthy();
  });
});
