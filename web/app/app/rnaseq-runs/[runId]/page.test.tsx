// @vitest-environment jsdom
/** A run's own page: who started it is "you" or another member's short id, never an email. */

import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";

const state = vi.hoisted(() => ({
  user: null as { id: string } | null,
  run: null as unknown,
  reports: new Set<string>(),
  datasetName: null as string | null,
}));
vi.mock("@/lib/supabase/server", () => ({
  createServerSupabaseClient: async () => ({}),
  getUser: async () => state.user,
}));
vi.mock("@/lib/rnaseq-runs", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/rnaseq-runs")>()),
  fetchRun: async () => state.run,
  fetchGenomeLabel: async () => null,
  fetchDatasetName: async () => state.datasetName,
}));
vi.mock("../species-labels", () => ({ speciesLabelMap: async () => ({}) }));
vi.mock("@/lib/rnaseq-reports", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/rnaseq-reports")>()),
  storedReports: async () => state.reports,
}));
vi.mock("../RnaseqRunView", () => ({
  default: ({
    startedBy,
    reportHref,
    loadedDatasetName,
  }: {
    startedBy: string;
    reportHref: string | null;
    loadedDatasetName: string | null;
  }) => (
    <>
      <p>Started by {startedBy}</p>
      <p>Report: {reportHref ?? "none"}</p>
      <p>Loaded as: {loadedDatasetName ?? "none"}</p>
    </>
  ),
}));
vi.mock("next/navigation", () => ({
  notFound: () => {
    throw new Error("not found");
  },
}));

import RnaseqRunPage from "./page";

afterEach(cleanup);

const OTHER = "4965b3af-ccfe-40f5-814b-447e1f726e1b";

async function show(requestedBy: string, userId: string | null) {
  state.user = userId ? { id: userId } : null;
  state.run = { id: 8, requested_by: requestedBy, metadata: {}, params: {} };
  render(await RnaseqRunPage({ params: Promise.resolve({ runId: "8" }) }));
}

describe("an RNA-seq run's page", () => {
  it("says you started your own run", async () => {
    await show("me", "me");
    expect(screen.getByText("Started by you")).toBeTruthy();
  });

  it("names another member's run by a short id, with no email", async () => {
    await show(OTHER, "me");
    expect(screen.getByText("Started by another member · 4965b3af")).toBeTruthy();
    expect(screen.queryByText(/@/)).toBeNull();
  });

  it("is not found for a run that can't be read", async () => {
    state.run = null;
    await expect(RnaseqRunPage({ params: Promise.resolve({ runId: "8" }) })).rejects.toThrow("not found");
  });

  it("links the run's Cell Ranger report once one is stored", async () => {
    state.reports = new Set(["web_summary.html", "metrics_summary.csv"]);
    await show("me", "me");
    expect(screen.getByText("Report: /app/rnaseq-runs/8/report")).toBeTruthy();
  });

  it("passes on the name the run's dataset was loaded under", async () => {
    state.datasetName = "Root atlas_v2";
    await show("me", "me");
    expect(screen.getByText("Loaded as: Root atlas_v2")).toBeTruthy();
    state.datasetName = null;
  });

  it("links no report when the run stored none, or only its metrics", async () => {
    state.reports = new Set(["metrics_summary.csv"]);
    await show("me", "me");
    expect(screen.getByText("Report: none")).toBeTruthy();
  });
});
