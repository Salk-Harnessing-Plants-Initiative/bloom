// @vitest-environment jsdom
/**
 * The experiment page's runs panel (add-cyl-pipeline-ui task 8.1): the 10
 * most recent runs touching the experiment, kept current by Realtime, with a
 * membership rule for runs it doesn't hold.
 */

import { StrictMode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import {
  deferred,
  liveChannels,
  queriesFor,
  resetSupabaseMock,
  supabaseMock,
  type Answer,
  type RecordedQuery,
} from "@/lib/cyl-pipeline/__fixtures__/supabase-mock";
import { at, runRow } from "@/lib/cyl-pipeline/__fixtures__/rows";
import type { RunRow } from "@/lib/cyl-pipeline/realtime-reducer";

vi.mock("@/lib/supabase/client", async () => (await import("@/lib/cyl-pipeline/__fixtures__/supabase-mock")).clientModule);

import { ExperimentRunsPanel } from "./ExperimentRunsPanel";

/** Runs in the database, and which of them touch experiment 5. */
let runs: RunRow[] = [];
let members = new Set<number>();
let viewError: Answer["error"] = null;
const fetchSpy = vi.fn();

function respond(q: RecordedQuery): Answer {
  if (q.table === "cyl_pipeline_run_experiments") {
    if (viewError) return { data: null, error: viewError };
    const inIds = q.arg("in")?.[1] as number[] | undefined;
    if (inIds) return { data: inIds.filter((id) => members.has(id)).map((run_id) => ({ run_id })), error: null };
    const touching = runs.filter((r) => members.has(r.id)).sort((a, b) => b.id - a.id).slice(0, 10);
    return { data: touching.map((r) => ({ run_id: r.id, created_at: r.created_at })), error: null };
  }
  if (q.table === "cyl_pipeline_runs") {
    const ids = q.arg("in")![1] as number[];
    return { data: runs.filter((r) => ids.includes(r.id)), error: null };
  }
  return { data: [], error: null };
}

const mount = (strict = false) => {
  const el = <ExperimentRunsPanel experimentId={5} />;
  return render(strict ? <StrictMode>{el}</StrictMode> : el);
};
const channel = () => liveChannels()[0];
const tick = (ms = 0) => act(() => vi.advanceTimersByTimeAsync(ms));
async function subscribe() {
  await act(async () => channel().status("SUBSCRIBED"));
  await tick();
}
const emit = (eventType: "INSERT" | "UPDATE", row: Partial<RunRow>) =>
  act(async () => channel().emit({ table: "cyl_pipeline_runs", eventType, new: row }));
const shown = () => screen.queryAllByTestId(/^panel-run-\d+$/).map((el) => Number(el.dataset.testid!.slice(10)));
const membershipQueries = () => queriesFor("cyl_pipeline_run_experiments").filter((q) => q.arg("in"));

beforeEach(() => {
  vi.useFakeTimers();
  vi.setSystemTime(new Date("2026-09-28T10:30:00Z"));
  runs = Array.from({ length: 12 }, (_, i) => runRow(i + 1, at(i + 1)));
  members = new Set(runs.map((r) => r.id));
  viewError = null;
  resetSupabaseMock(respond);
  fetchSpy.mockReset();
  vi.stubGlobal("fetch", fetchSpy);
});
afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
  cleanup();
});

describe("the runs it lists", () => {
  it("shows the 10 most recent runs touching the experiment, with their display states", async () => {
    runs[11] = runRow(12, at(12), { status: "complete", done_count: 37, failed_count: 3 });
    mount();
    await subscribe();
    expect(shown()).toEqual([12, 11, 10, 9, 8, 7, 6, 5, 4, 3]);
    const [q] = queriesFor("cyl_pipeline_run_experiments");
    expect(q.arg("eq")).toEqual(["experiment_id", 5]);
    expect(q.arg("limit")).toEqual([10]);
    expect(screen.getByTestId("panel-run-12").textContent).toContain("Finished · 37 succeeded · 3 failed");
  });

  it("links each run to its drill-down, and to all cylinder pipeline runs, under a cylinder heading", async () => {
    mount();
    await subscribe();
    expect(within(screen.getByTestId("panel-run-12")).getByRole("link", { name: /Run 12/ }).getAttribute("href")).toBe(
      "/app/cyl-pipeline-runs/12",
    );
    expect(screen.getByRole("link", { name: "All cylinder pipeline runs" }).getAttribute("href")).toBe("/app/cyl-pipeline-runs");
    expect(screen.getByRole("heading", { name: "Cylinder pipeline runs" })).toBeTruthy();
  });

  it("says when no run touches the experiment yet", async () => {
    members = new Set();
    mount();
    await subscribe();
    expect(screen.getByText(/No pipeline runs include this experiment's scans yet/)).toBeTruthy();
  });

  it("shows Runs unavailable when the view query fails, without throwing, and Retry reloads", async () => {
    viewError = { message: 'relation "public.cyl_pipeline_run_experiments" does not exist', code: "42P01" };
    mount();
    await subscribe();
    expect(screen.getByText("Runs unavailable")).toBeTruthy();
    viewError = null;
    await act(async () => fireEvent.click(screen.getByRole("button", { name: "Retry" })));
    await tick();
    expect(screen.queryByText("Runs unavailable")).toBeNull();
    expect(shown()).toHaveLength(10);
  });

  it("says live updates are unavailable, not loading, when the channel fails first, and Refresh loads the runs", async () => {
    mount();
    await act(async () => channel().status("CHANNEL_ERROR"));
    expect(screen.queryByText(/Loading runs/)).toBeNull();
    expect(screen.getByText(/Live updates are unavailable/)).toBeTruthy();
    await act(async () => fireEvent.click(screen.getAllByRole("button", { name: /refresh/i })[0]));
    await tick();
    expect(shown()).toHaveLength(10);
  });
});

describe("live events", () => {
  it("applies events for held runs with no query", async () => {
    mount();
    await subscribe();
    const before = supabaseMock.queries.length;
    await emit("UPDATE", { id: 12, done_count: 13 });
    await tick(5000);
    expect(screen.getByTestId("panel-run-12").textContent).toContain("13 / 40 succeeded");
    expect(supabaseMock.queries.length).toBe(before);
  });

  it("stays at 10 when a newly listed run arrives, adding it from its payload with no extra read", async () => {
    mount();
    await subscribe();
    const reads = queriesFor("cyl_pipeline_runs").length;
    const fresh = runRow(13, at(13), { status: "submitted" });
    runs.push(fresh);
    members.add(13);
    await emit("UPDATE", fresh);
    await tick(1000);
    expect(shown()).toEqual([13, 12, 11, 10, 9, 8, 7, 6, 5, 4]);
    expect(queriesFor("cyl_pipeline_runs").length).toBe(reads);
  });

  it("reads a confirmed member by id when its payload was only partial", async () => {
    mount();
    await subscribe();
    runs.push(runRow(13, at(13), { status: "running", done_count: 4 }));
    members.add(13);
    await emit("UPDATE", { id: 13, done_count: 4, status: "running" });
    await tick(1000);
    const last = queriesFor("cyl_pipeline_runs").at(-1)!;
    expect(last.arg("in")).toEqual(["id", [13]]);
    expect(shown()[0]).toBe(13);
    expect(screen.getByTestId("panel-run-13").textContent).toContain("4 / 40 succeeded");
  });

  it("lists a run once its scan rows land: an empty answer to a queued INSERT isn't cached", async () => {
    mount();
    await subscribe();
    const fresh = runRow(95, at(95), { status: "queued" });
    runs.push(fresh);
    await emit("INSERT", fresh);
    await tick(1000);
    expect(membershipQueries()).toHaveLength(1);
    expect(membershipQueries()[0].arg("in")).toEqual(["run_id", [95]]);
    expect(shown()).not.toContain(95);

    members.add(95);
    await emit("UPDATE", { ...fresh, status: "submitted" });
    await tick(1000);
    expect(membershipQueries()).toHaveLength(2);
    expect(shown()[0]).toBe(95);
  });

  it("never lets a late membership answer overwrite a run it already shows with an older state", async () => {
    mount();
    await subscribe();
    const answer = deferred<Answer>();
    supabaseMock.respond = (q) => (q.table === "cyl_pipeline_run_experiments" && q.arg("in") ? answer.promise : respond(q));
    const r = runRow(99, at(99), { status: "submitted" });
    runs.push(r);
    members.add(99);
    await emit("UPDATE", r);
    await tick(1000); // flush #1 asks, and waits
    await emit("UPDATE", { ...r, status: "running" }); // queued again: not yet a member
    await act(async () => answer.resolve({ data: [{ run_id: 99 }], error: null }));
    await tick();
    supabaseMock.respond = respond;
    // Counts that haven't settled, so the label follows the status.
    await emit("UPDATE", { ...r, status: "failed", failed_count: 5, error_message: "dispatch rejected" }); // held now: merged
    expect(screen.getByTestId("panel-run-99").textContent).toContain("Failed · 0 succeeded · 5 failed · 35 without a result");
    await tick(1000); // flush #2 would re-add the "running" payload
    expect(screen.getByTestId("panel-run-99").textContent).toContain("Failed · 0 succeeded · 5 failed · 35 without a result");
  });

  it("debounces the membership query by 1 s", async () => {
    mount();
    await subscribe();
    await emit("UPDATE", runRow(96, at(96), { status: "running" }));
    await tick(999);
    expect(membershipQueries()).toHaveLength(0);
    await tick(1);
    expect(membershipQueries()).toHaveLength(1);
  });

  it("asks once about a foreign running run, then never again", async () => {
    mount();
    await subscribe();
    const foreign = runRow(99, at(99), { status: "running" });
    for (let i = 0; i < 10; i++) {
      await emit("UPDATE", { ...foreign, done_count: i });
      await tick(i < 5 ? 100 : 1500);
    }
    expect(membershipQueries()).toHaveLength(1);
    expect(shown()).not.toContain(99);
  });
});

describe("sync", () => {
  it("refetches once on the first SUBSCRIBED, and once more at the end of the window after a reconnect", async () => {
    mount();
    expect(supabaseMock.queries).toHaveLength(0);
    await subscribe();
    expect(queriesFor("cyl_pipeline_run_experiments")).toHaveLength(1);
    await tick(500);
    await act(async () => {
      channel().status("CLOSED");
      channel().status("SUBSCRIBED");
    });
    await tick(1499);
    expect(queriesFor("cyl_pipeline_run_experiments")).toHaveLength(1);
    await tick(1);
    expect(queriesFor("cyl_pipeline_run_experiments")).toHaveLength(2);
  });

  it("keeps an event that arrives during the snapshot fetch", async () => {
    mount();
    const pending = deferred<Answer>();
    supabaseMock.respond = (q) => (q.table === "cyl_pipeline_runs" ? pending.promise : respond(q));
    await act(async () => channel().status("SUBSCRIBED"));
    await tick();
    await emit("UPDATE", { id: 12, done_count: 13 });
    await act(async () => pending.resolve({ data: runs.slice(2), error: null }));
    await tick();
    expect(screen.getByTestId("panel-run-12").textContent).toContain("13 / 40 succeeded");
  });

  it("shows connecting, live, then offline with a refresh", async () => {
    mount();
    expect(screen.getByRole("status").textContent).toMatch(/connecting/i);
    await subscribe();
    expect(screen.getByRole("status").textContent).toBe("Live");
    await act(async () => channel().status("CHANNEL_ERROR"));
    const before = queriesFor("cyl_pipeline_run_experiments").length;
    await act(async () => fireEvent.click(screen.getByRole("button", { name: /refresh/i })));
    await tick();
    expect(queriesFor("cyl_pipeline_run_experiments").length).toBe(before + 1);
  });

  it("under StrictMode keeps one channel, and removes it on unmount", () => {
    const view = mount(true);
    expect(supabaseMock.channels).toHaveLength(2);
    expect(liveChannels()).toHaveLength(1);
    view.unmount();
    expect(liveChannels()).toHaveLength(0);
  });

  it("issues no queries and no workflows requests over five quiet minutes", async () => {
    mount();
    await subscribe();
    await tick(2000);
    const before = supabaseMock.queries.length;
    await tick(300_000);
    expect(supabaseMock.queries.length).toBe(before);
    expect(fetchSpy).not.toHaveBeenCalled();
  });
});
