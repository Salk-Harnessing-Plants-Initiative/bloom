// @vitest-environment jsdom
/** A run's own page: who started it is "you" or another member's short id, never an email. */

import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";

const state = vi.hoisted(() => ({ user: null as { id: string } | null, run: null as unknown }));
vi.mock("@/lib/supabase/server", () => ({
  createServerSupabaseClient: async () => ({}),
  getUser: async () => state.user,
}));
vi.mock("@/lib/rnaseq-runs", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/rnaseq-runs")>()),
  fetchRun: async () => state.run,
  fetchGenomeLabel: async () => null,
}));
vi.mock("../species-labels", () => ({ speciesLabelMap: async () => ({}) }));
vi.mock("../RnaseqRunView", () => ({
  default: ({ startedBy }: { startedBy: string }) => <p>Started by {startedBy}</p>,
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
});
