// @vitest-environment jsdom
/**
 * The shared runs list at /app/cyl-pipeline-runs (add-cyl-pipeline-ui task
 * 6.1): Realtime sync without polling, the keyset window, "Only mine", and
 * row contents.
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
import { at, ME, OTHER, runRow } from "@/lib/cyl-pipeline/__fixtures__/rows";
import type { RunRow } from "@/lib/cyl-pipeline/realtime-reducer";
import type { RunExperiment } from "@/lib/cyl-pipeline/queries";

vi.mock("@/lib/supabase/client", async () => (await import("@/lib/cyl-pipeline/__fixtures__/supabase-mock")).clientModule);

import { RunsListLive } from "./RunsListLive";

/** What the database holds, newest first; the responder pages it like PostgREST. */
let db: RunRow[] = [];
let names: RunExperiment[] = [];
const fetchSpy = vi.fn();

function respond(q: RecordedQuery): Answer | Promise<Answer> {
  if (q.table === "cyl_pipeline_runs") {
    let rows = [...db];
    const mine = q.all("eq").find(([col]) => col === "requested_by");
    if (mine) rows = rows.filter((r) => r.requested_by === mine[1]);
    const inIds = q.arg("in");
    if (inIds) rows = rows.filter((r) => (inIds[1] as number[]).includes(r.id));
    const or = q.arg("or");
    if (or) {
      const [, ts, id] = /created_at\.lt\."([^"]+)",and\(created_at\.eq\."[^"]+",id\.lt\.(\d+)\)/.exec(String(or[0]))!;
      rows = rows.filter((r) => r.created_at < ts || (r.created_at === ts && r.id < Number(id)));
    }
    const limit = (q.arg("limit")?.[0] as number | undefined) ?? rows.length;
    return { data: rows.slice(0, limit), error: null };
  }
  if (q.table === "cyl_pipeline_run_experiments") {
    const ids = (q.arg("in")?.[1] as number[]) ?? [];
    return {
      data: names
        .filter((n) => ids.includes(n.run_id))
        .map(({ name, species_id, ...rest }) => ({ ...rest, cyl_experiments: { name, species_id } })),
      error: null,
    };
  }
  return { data: [], error: null };
}

function mount(props: Partial<Parameters<typeof RunsListLive>[0]> = {}, strict = false) {
  const el = (
    <RunsListLive
      initialRuns={props.initialRuns ?? db.slice(0, 50)}
      initialExperiments={props.initialExperiments ?? names}
      currentUserId={props.currentUserId ?? ME}
      initialError={props.initialError ?? null}
    />
  );
  return render(strict ? <StrictMode>{el}</StrictMode> : el);
}

const channel = () => liveChannels()[0];
const tick = (ms = 0) => act(() => vi.advanceTimersByTimeAsync(ms));
async function subscribe() {
  await act(async () => channel().status("SUBSCRIBED"));
  await tick();
}
const emit = (eventType: "INSERT" | "UPDATE", row: Partial<RunRow>) =>
  act(async () => channel().emit({ table: "cyl_pipeline_runs", eventType, new: row }));
const row = (id: number) => screen.getByTestId(`run-${id}`);
const rowIds = () => screen.queryAllByTestId(/^run-\d+$/).map((el) => Number(el.dataset.testid!.slice(4)));

beforeEach(() => {
  vi.useFakeTimers();
  vi.setSystemTime(new Date("2026-09-28T10:30:00Z"));
  db = [];
  names = [];
  resetSupabaseMock(respond);
  fetchSpy.mockReset();
  vi.stubGlobal("fetch", fetchSpy);
});
afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
  cleanup();
});

describe("subscriptions and resync", () => {
  it("subscribes to run INSERTs and UPDATEs on a topic of its own", () => {
    mount();
    const [ch] = supabaseMock.channels;
    expect(ch.topic).toMatch(/^cyl-pipeline-runs/);
    expect(ch.bindings.map((b) => [b.type, b.filter])).toEqual([
      ["postgres_changes", { event: "INSERT", schema: "public", table: "cyl_pipeline_runs" }],
      ["postgres_changes", { event: "UPDATE", schema: "public", table: "cyl_pipeline_runs" }],
    ]);
  });

  it("refetches once on the first SUBSCRIBED, and once more at the end of the window after a reconnect", async () => {
    db = [runRow(91, at(1))];
    mount();
    expect(queriesFor("cyl_pipeline_runs")).toHaveLength(0);
    await subscribe();
    expect(queriesFor("cyl_pipeline_runs")).toHaveLength(1);

    await tick(500);
    await act(async () => {
      channel().status("CLOSED");
      channel().status("SUBSCRIBED");
    });
    await tick(1499);
    expect(queriesFor("cyl_pipeline_runs")).toHaveLength(1);
    await tick(1);
    expect(queriesFor("cyl_pipeline_runs")).toHaveLength(2);
  });

  it("shows the newer value when an UPDATE arrives during the snapshot fetch", async () => {
    db = [runRow(91, at(1), { done_count: 12 })];
    mount();
    const pending = deferred<Answer>();
    supabaseMock.respond = (q) => (q.table === "cyl_pipeline_runs" ? pending.promise : respond(q));
    await act(async () => channel().status("SUBSCRIBED"));
    await emit("UPDATE", { id: 91, done_count: 13, scan_count: 40, status: "running", created_at: at(1) });
    await act(async () => pending.resolve({ data: [runRow(91, at(1), { done_count: 12 })], error: null }));
    await tick();
    expect(row(91).textContent).toContain("13 / 40 succeeded");
  });

  it("applies an UPDATE for a held run without any query", async () => {
    db = [runRow(91, at(1), { done_count: 12 })];
    mount();
    await subscribe();
    const before = supabaseMock.queries.length;
    await emit("UPDATE", { id: 91, done_count: 13 });
    expect(row(91).textContent).toContain("13 / 40 succeeded");
    expect(supabaseMock.queries.length).toBe(before);
  });

  it("puts a new run on top with no names and no query, then looks its names up once on its first non-queued event", async () => {
    db = [runRow(90, at(1))];
    names = [{ run_id: 90, experiment_id: 5, created_at: at(1), name: "exp-five", species_id: 2 }];
    mount();
    await subscribe();
    const before = supabaseMock.queries.length;

    const fresh = runRow(95, at(100), { requested_by: OTHER, status: "queued" });
    await emit("INSERT", fresh);
    expect(rowIds()[0]).toBe(95);
    expect(row(95).textContent).toContain("another member · 0b7e2c91");
    expect(within(row(95)).queryByRole("link", { name: /exp-/ })).toBeNull();
    expect(supabaseMock.queries.length).toBe(before);

    names.push({ run_id: 95, experiment_id: 5, created_at: at(100), name: "exp-five", species_id: 2 });
    await emit("UPDATE", { id: 95, status: "queued" });
    expect(queriesFor("cyl_pipeline_run_experiments")).toHaveLength(1);
    await emit("UPDATE", { id: 95, status: "submitted" });
    await tick();
    expect(queriesFor("cyl_pipeline_run_experiments")).toHaveLength(2);
    expect(queriesFor("cyl_pipeline_run_experiments")[1].arg("in")).toEqual(["run_id", [95]]);
    expect(within(row(95)).getByRole("link", { name: "exp-five" }).getAttribute("href")).toBe("/app/phenotypes/2/5");

    await emit("UPDATE", { id: 95, status: "running" });
    await tick();
    expect(queriesFor("cyl_pipeline_run_experiments")).toHaveLength(2);
  });

  it("ignores an unloaded older run, and returns it in order on load older, with nothing skipped or duplicated", async () => {
    db = Array.from({ length: 60 }, (_, i) => runRow(300 - i, at(600 - i * 5)));
    mount();
    await subscribe();
    expect(rowIds()).toHaveLength(50);

    const older = db[55];
    await emit("UPDATE", { ...older, done_count: 2 });
    expect(rowIds()).not.toContain(older.id);

    await act(async () => fireEvent.click(screen.getByRole("button", { name: /load older/i })));
    await tick();
    expect(rowIds()).toEqual(db.map((r) => r.id));
    expect(screen.queryByRole("button", { name: /load older/i })).toBeNull();
    const [, olderQuery] = queriesFor("cyl_pipeline_runs");
    expect(olderQuery.arg("or")).toEqual([
      `created_at.lt."${db[49].created_at}",and(created_at.eq."${db[49].created_at}",id.lt.${db[49].id})`,
    ]);
  });
});

describe("races between load older and a resync", () => {
  it("drops a load-older page when a resync lands first, so no run is skipped", async () => {
    // Snapshot: runs 60..11. Runs 61..63 arrive live. A reconnect resync then
    // replaces the window with 63..14 while the older page (10..1) is in flight.
    db = Array.from({ length: 50 }, (_, i) => runRow(60 - i, at(600 - i * 5)));
    mount();
    await subscribe();
    const older = deferred<Answer>();
    supabaseMock.respond = (q) => (q.table === "cyl_pipeline_runs" && q.arg("or") ? older.promise : respond(q));
    await act(async () => fireEvent.click(screen.getByRole("button", { name: /load older/i })));

    db = [...[63, 62, 61].map((id) => runRow(id, at(700 + id))), ...db];
    await tick(2000);
    await act(async () => {
      channel().status("CLOSED");
      channel().status("SUBSCRIBED");
    });
    await tick();
    expect(rowIds().slice(0, 3)).toEqual([63, 62, 61]);
    expect(rowIds()).toHaveLength(50);

    await act(async () =>
      older.resolve({ data: Array.from({ length: 10 }, (_, i) => runRow(10 - i, at(300 - i * 5))), error: null }),
    );
    await tick();
    // The stale page is dropped: 13, 12 and 11 are still reachable by loading older again.
    expect(rowIds()).toHaveLength(50);
    expect(rowIds().at(-1)).toBe(14);
    expect(screen.getByRole("button", { name: /load older/i })).toBeTruthy();
  });

  it("disables load older while a snapshot is in flight", async () => {
    db = Array.from({ length: 50 }, (_, i) => runRow(60 - i, at(600 - i * 5)));
    mount();
    await subscribe();
    const pending = deferred<Answer>();
    supabaseMock.respond = (q) => (q.table === "cyl_pipeline_runs" ? pending.promise : respond(q));
    // A reconnect starts a snapshot that stays in flight.
    await tick(2000);
    await act(async () => {
      channel().status("CLOSED");
      channel().status("SUBSCRIBED");
    });
    expect((screen.getByRole("button", { name: /load older/i }) as HTMLButtonElement).disabled).toBe(true);
    await act(async () => pending.resolve({ data: db, error: null }));
    await tick();
    expect((screen.getByRole("button", { name: /load older/i }) as HTMLButtonElement).disabled).toBe(false);
  });

  it("ignores a load-older click that lands in the same tick a resync starts, before the button re-renders", async () => {
    db = Array.from({ length: 50 }, (_, i) => runRow(60 - i, at(600 - i * 5)));
    mount();
    await subscribe();
    await tick(2000);
    const before = queriesFor("cyl_pipeline_runs").filter((q) => q.arg("or")).length;
    await act(async () => {
      channel().status("CLOSED");
      channel().status("SUBSCRIBED"); // starts a snapshot synchronously
      fireEvent.click(screen.getByRole("button", { name: /load older/i })); // still enabled in the DOM
    });
    await tick();
    expect(queriesFor("cyl_pipeline_runs").filter((q) => q.arg("or")).length).toBe(before);
  });

  it("drops a load-older page when Only mine is toggled while it loads", async () => {
    db = Array.from({ length: 50 }, (_, i) => runRow(60 - i, at(600 - i * 5), { requested_by: i % 2 ? ME : OTHER }));
    mount();
    await subscribe();
    const older = deferred<Answer>();
    supabaseMock.respond = (q) => (q.table === "cyl_pipeline_runs" && q.arg("or") ? older.promise : respond(q));
    await act(async () => fireEvent.click(screen.getByRole("button", { name: /load older/i })));
    await act(async () => fireEvent.click(screen.getByRole("checkbox", { name: "Only mine" })));
    await tick();
    await act(async () => older.resolve({ data: [runRow(5, at(100), { requested_by: OTHER })], error: null }));
    await tick();
    expect(rowIds()).not.toContain(5);
  });

  // (characterization) The hook's generation guard already did this; the test keeps it covered.
  it("keeps only the newest snapshot when an older one resolves last (Only mine stays mine)", async () => {
    db = [runRow(92, at(2), { requested_by: OTHER }), runRow(91, at(1), { requested_by: ME })];
    mount();
    await subscribe();
    const unfiltered = deferred<Answer>();
    supabaseMock.respond = (q) =>
      q.table === "cyl_pipeline_runs" && !q.all("eq").some(([c]) => c === "requested_by") ? unfiltered.promise : respond(q);
    // A reconnect starts an unfiltered snapshot, then the user ticks Only mine.
    await tick(2000);
    await act(async () => {
      channel().status("CLOSED");
      channel().status("SUBSCRIBED");
    });
    await act(async () => fireEvent.click(screen.getByRole("checkbox", { name: "Only mine" })));
    await tick();
    expect(rowIds()).toEqual([91]);
    await act(async () => unfiltered.resolve({ data: db, error: null }));
    await tick();
    expect(rowIds()).toEqual([91]);
  });
});

describe("Only mine", () => {
  it("hides other members' runs as soon as it is ticked, before the filtered snapshot arrives", async () => {
    db = [runRow(92, at(2), { requested_by: OTHER }), runRow(91, at(1), { requested_by: ME })];
    mount();
    await subscribe();
    const pending = deferred<Answer>();
    supabaseMock.respond = (q) => (q.table === "cyl_pipeline_runs" ? pending.promise : respond(q));
    await act(async () => fireEvent.click(screen.getByRole("checkbox", { name: "Only mine" })));
    expect(rowIds()).toEqual([91]);
    await act(async () => pending.resolve({ data: [db[1]], error: null }));
    await tick();
    expect(rowIds()).toEqual([91]);
  });

  it("filters by requester on the server and ignores other members' live runs", async () => {
    db = [runRow(92, at(2), { requested_by: OTHER }), runRow(91, at(1), { requested_by: ME })];
    mount();
    await subscribe();
    expect(rowIds()).toEqual([92, 91]);

    await act(async () => fireEvent.click(screen.getByRole("checkbox", { name: "Only mine" })));
    await tick();
    const last = queriesFor("cyl_pipeline_runs").at(-1)!;
    expect(last.arg("eq")).toEqual(["requested_by", ME]);
    expect(rowIds()).toEqual([91]);

    await emit("INSERT", runRow(93, at(3), { requested_by: OTHER }));
    expect(rowIds()).toEqual([91]);
    await emit("INSERT", runRow(94, at(4), { requested_by: ME }));
    expect(rowIds()).toEqual([94, 91]);
  });
});

describe("experiment names", () => {
  it("looks a live run's names up at most once, even when that lookup fails", async () => {
    db = [runRow(90, at(1))];
    mount();
    await subscribe();
    const fresh = runRow(95, at(100), { status: "queued" });
    await emit("INSERT", fresh);
    supabaseMock.respond = (q) => (q.table === "cyl_pipeline_run_experiments" ? { data: null, error: { message: "timeout" } } : respond(q));
    const before = queriesFor("cyl_pipeline_run_experiments").length;
    await emit("UPDATE", { id: 95, status: "submitted" });
    await tick();
    await emit("UPDATE", { id: 95, status: "running" });
    await tick();
    expect(queriesFor("cyl_pipeline_run_experiments").length).toBe(before + 1);
  });
});

describe("connection state", () => {
  it("shows connecting, then live, then offline with a refresh that refetches", async () => {
    db = [runRow(91, at(1))];
    mount();
    expect(screen.getByRole("status").textContent).toMatch(/connecting/i);
    await subscribe();
    expect(screen.getByRole("status").textContent).toBe("Live");
    await act(async () => channel().status("CHANNEL_ERROR"));
    expect(screen.getByRole("status").textContent).toMatch(/offline/i);

    const before = queriesFor("cyl_pipeline_runs").length;
    await act(async () => fireEvent.click(screen.getByRole("button", { name: /refresh/i })));
    await tick();
    expect(queriesFor("cyl_pipeline_runs").length).toBe(before + 1);
  });
});

describe("lifecycle", () => {
  it("under StrictMode creates two topics, removes the first, and keeps one", () => {
    mount({}, true);
    expect(supabaseMock.channels).toHaveLength(2);
    expect(new Set(supabaseMock.channels.map((c) => c.topic)).size).toBe(2);
    expect(supabaseMock.removeChannel).toHaveBeenCalledTimes(1);
    expect(supabaseMock.removeChannel.mock.calls[0][0]).toBe(supabaseMock.channels[0]);
    expect(liveChannels()).toEqual([supabaseMock.channels[1]]);
  });

  it("removes its channel on unmount", () => {
    const view = mount();
    view.unmount();
    expect(liveChannels()).toHaveLength(0);
  });

  it("issues no queries and no workflows requests over five quiet minutes", async () => {
    db = [runRow(91, at(1))];
    mount();
    await subscribe();
    await tick(2000);
    const before = supabaseMock.queries.map((q) => q.table);
    await tick(300_000);
    expect(supabaseMock.queries.map((q) => q.table)).toEqual(before);
    expect(fetchSpy).not.toHaveBeenCalled();
  });
});

describe("table (bloom#955)", () => {
  it("heads its columns Run, Target, Experiments and State, one row per run", () => {
    db = [runRow(92, at(2)), runRow(91, at(1))];
    mount();
    const headers = screen.getAllByRole("columnheader");
    expect(headers.map((h) => h.textContent)).toEqual(["Run", "Target", "Experiments", "State"]);
    for (const h of headers) expect(h.getAttribute("scope")).toBe("col");
    expect(row(91).tagName).toBe("TR");
    expect(within(screen.getByRole("table")).getAllByRole("row")).toHaveLength(3);
  });

  it("renders no table when no run is listed", () => {
    mount({ initialRuns: [] });
    expect(screen.getByText("No pipeline runs yet")).toBeTruthy();
    expect(screen.queryByRole("table")).toBeNull();
  });

  it("renders no table when Only mine has no runs", async () => {
    db = [runRow(91, at(1), { requested_by: OTHER })];
    mount();
    await act(async () => fireEvent.click(screen.getByRole("checkbox", { name: /only mine/i })));
    await tick();
    expect(screen.getByText("You haven't started any pipeline runs")).toBeTruthy();
    expect(screen.queryByRole("table")).toBeNull();
  });
});

describe("row contents", () => {
  it("shows the requester, target, experiment links and elapsed time", async () => {
    db = [
      runRow(93, at(3), { target_level: "scan_ids", target_id: null, scan_count: 3, requested_by: ME }),
      runRow(92, at(2), { target_level: "scan", target_id: 577, scan_count: 1, requested_by: OTHER }),
      runRow(91, at(1), { target_level: "experiment", target_id: 5, requested_by: null }),
    ];
    names = [
      { run_id: 92, experiment_id: 5, created_at: at(2), name: "exp-five", species_id: 2 },
      { run_id: 92, experiment_id: 6, created_at: at(2), name: "exp-six", species_id: 3 },
    ];
    mount();

    expect(row(93).textContent).toContain("3 selected scans");
    expect(row(93).textContent).toContain("you");
    expect(within(row(93)).getByRole("link", { name: /Run 93/ }).getAttribute("href")).toBe("/app/cyl-pipeline-runs/93");
    expect(row(92).textContent).toContain("scan 577 · 1 scan");
    expect(row(92).textContent).toContain("another member · 0b7e2c91");
    expect(within(row(92)).getByRole("link", { name: "exp-six" }).getAttribute("href")).toBe("/app/phenotypes/3/6");
    expect(row(91).textContent).toContain("experiment 5 · 40 scans");
    await tick();
    expect(row(91).textContent).toContain("requested 29 min ago");
  });

  it("shows no experiment link for a run whose only experiment is soft-deleted (the view has no row)", () => {
    db = [runRow(91, at(1))];
    names = [];
    mount();
    expect(within(row(91)).queryAllByRole("link").map((a) => a.getAttribute("href"))).toEqual(["/app/cyl-pipeline-runs/91"]);
  });

  it("shows the display state and raw status, and links the failed count to the drill-down", () => {
    db = [runRow(91, at(1), { status: "complete", done_count: 37, failed_count: 3 })];
    mount();
    expect(row(91).textContent).toContain("Finished · 37 succeeded · 3 failed");
    expect(row(91).textContent).toContain("complete");
    expect(within(row(91)).getByRole("link", { name: "3 failed" }).getAttribute("href")).toBe("/app/cyl-pipeline-runs/91?status=failed");
  });

  it("does not link a failed count of zero (bloom#955)", () => {
    db = [runRow(7, at(1), { status: "failed", scan_count: 3, done_count: 0, failed_count: 0 })];
    mount();
    expect(row(7).textContent).toContain("Failed · 0 succeeded · 0 failed · 3 without a result");
    expect(within(row(7)).queryByRole("link", { name: "0 failed" })).toBeNull();
  });

  it("shows a failed run's error message", () => {
    db = [runRow(91, at(1), { status: "failed", failed_count: 40, error_message: "dispatch rejected" })];
    mount();
    expect(row(91).textContent).toContain("dispatch rejected");
  });
});

describe("empty and error states", () => {
  it("says there are no runs yet", () => {
    mount({ initialRuns: [] });
    expect(screen.getByText("No pipeline runs yet")).toBeTruthy();
  });

  it("shows the snapshot error instead of an empty list", () => {
    mount({ initialRuns: [], initialError: "connection refused" });
    expect(screen.getByRole("alert").textContent).toContain("connection refused");
    expect(screen.queryByText("No pipeline runs yet")).toBeNull();
  });

  it("shows a failed resync's error, and clears it on the next good one", async () => {
    db = [runRow(91, at(1))];
    mount();
    supabaseMock.respond = () => ({ data: null, error: { message: "timeout" } });
    await subscribe();
    expect(screen.getByRole("alert").textContent).toContain("timeout");
    expect(row(91)).toBeTruthy();
    expect(within(screen.getByRole("table")).getByTestId("run-91")).toBeTruthy();
    supabaseMock.respond = respond;
    await act(async () => fireEvent.click(screen.getByRole("button", { name: /retry/i })));
    await tick();
    expect(screen.queryByRole("alert")).toBeNull();
  });
});
