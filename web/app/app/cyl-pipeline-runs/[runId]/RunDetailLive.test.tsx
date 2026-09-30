// @vitest-environment jsdom
/**
 * The per-run drill-down (add-cyl-pipeline-ui task 7.2): per-scan rows kept
 * current by Realtime, a header counted from those rows, failure hints, and
 * links that come only from the run's requested scans.
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
import { at, runRow, scanMeta, scanRow } from "@/lib/cyl-pipeline/__fixtures__/rows";
import { BACKSTOP_MESSAGE, NO_OP_NOTE } from "@/lib/cyl-pipeline/failure-hints";
import type { RunRow, RunScanRow } from "@/lib/cyl-pipeline/realtime-reducer";
import type { ScanMeta } from "@/lib/cyl-pipeline/scan-meta";
import type { ScanTableRow } from "./RunScansTable";
import type { RunPipelineDialogProps } from "@/components/cyl-pipeline/RunPipelineDialog";

vi.mock("@/lib/supabase/client", async () => (await import("@/lib/cyl-pipeline/__fixtures__/supabase-mock")).clientModule);
// The re-run actions' dialog: the real button, a dialog that only records its target.
const dialog = vi.hoisted(() => ({ props: null as RunPipelineDialogProps | null }));
vi.mock("@/components/cyl-pipeline/RunPipelineDialog", () => ({
  RunPipelineDialog: (props: RunPipelineDialogProps) => {
    dialog.props = props;
    return <div role="dialog" />;
  },
}));
vi.mock("./RunScansTable", () => ({
  RunScansTable: ({ rows, initialFilter }: { rows: ScanTableRow[]; initialFilter?: string }) => (
    <div data-testid="table" data-count={rows.length} data-filter={initialFilter}>
      {rows.slice(0, 20).map((r) => (
        <div key={r.id} data-testid={`scan-${r.scan_id}`}>
          {r.statusLabel} | current={String(r.current)} | {r.likelyCause ?? ""} | {r.noOpNote ?? ""} | {r.qr_code ?? ""}
          {r.scanHref && <a href={r.scanHref}>Scan images</a>}
        </div>
      ))}
    </div>
  ),
}));

import { RunDetailLive } from "./RunDetailLive";

let run: RunRow;
let scans: RunScanRow[];
let meta: ScanMeta[];
let latest: { scan_id: number; max_source_id: number | null }[];
let experiments: { run_id: number; experiment_id: number; created_at: string; cyl_experiments: { name: string; species_id: number } }[];
const fetchSpy = vi.fn();

function respond(q: RecordedQuery): Answer {
  const ids = (q.arg("in")?.[1] as number[] | undefined) ?? [];
  switch (q.table) {
    case "cyl_pipeline_runs":
      return { data: run, error: null };
    case "cyl_pipeline_run_scans": {
      const [from, to] = q.arg("range") as [number, number];
      return { data: scans.slice(from, to + 1), error: null };
    }
    case "cyl_scans_extended":
      return { data: meta.filter((m) => ids.includes(m.scan_id)), error: null };
    case "cyl_scan_latest_source":
      return { data: latest.filter((l) => ids.includes(l.scan_id)), error: null };
    case "cyl_pipeline_run_experiments":
      return { data: experiments, error: null };
  }
  return { data: [], error: null };
}

const mount = (strict = false, initialFilter: "all" | "failed" = "all") => {
  const el = <RunDetailLive initialRun={run} initialFilter={initialFilter} />;
  return render(strict ? <StrictMode>{el}</StrictMode> : el);
};
const channel = () => liveChannels()[0];
const tick = (ms = 0) => act(() => vi.advanceTimersByTimeAsync(ms));
async function subscribe() {
  await act(async () => channel().status("SUBSCRIBED"));
  await tick();
}
const emitScan = (eventType: "INSERT" | "UPDATE", row: Partial<RunScanRow>) =>
  act(async () => channel().emit({ table: "cyl_pipeline_run_scans", eventType, new: row }));
const emitRun = (row: Partial<RunRow>) => act(async () => channel().emit({ table: "cyl_pipeline_runs", eventType: "UPDATE", new: row }));
const header = () => screen.getByTestId("run-header");
const scanEl = (scanId: number) => screen.getByTestId(`scan-${scanId}`);

beforeEach(() => {
  vi.useFakeTimers();
  vi.setSystemTime(new Date("2026-09-28T10:30:00Z"));
  run = runRow(91, at(0), { scan_count: 2, status: "running", target_level: "scan_ids", target_id: null });
  scans = [scanRow(1, 577), scanRow(2, 578)];
  meta = [scanMeta(577), scanMeta(578)];
  latest = [];
  experiments = [{ run_id: 91, experiment_id: 5, created_at: at(0), cyl_experiments: { name: "exp-five", species_id: 2 } }];
  resetSupabaseMock(respond);
  fetchSpy.mockReset();
  vi.stubGlobal("fetch", fetchSpy);
  dialog.props = null;
});
afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
  cleanup();
});

describe("subscriptions", () => {
  it("filters the run to its id and the scan rows to its run_id, on a topic of its own", () => {
    mount();
    const [ch] = supabaseMock.channels;
    expect(ch.topic).toMatch(/^cyl-pipeline-run-91/);
    expect(ch.bindings.map((b) => b.filter)).toEqual([
      { event: "UPDATE", schema: "public", table: "cyl_pipeline_runs", filter: "id=eq.91" },
      { event: "INSERT", schema: "public", table: "cyl_pipeline_run_scans", filter: "run_id=eq.91" },
      { event: "UPDATE", schema: "public", table: "cyl_pipeline_run_scans", filter: "run_id=eq.91" },
    ]);
  });
});

describe("live rows and the header", () => {
  it("relabels a scan that turns written, and the header's succeeded count follows", async () => {
    mount();
    await subscribe();
    expect(scanEl(577).textContent).toContain("Waiting");
    expect(header().textContent).toContain("Running · 0 / 2 succeeded");

    await emitScan("UPDATE", { id: 1, run_id: 91, scan_id: 577, status: "written", source_id: 900, updated_at: at(120) });
    expect(scanEl(577).textContent).toContain("Result recorded");
    expect(header().textContent).toContain("Running · 1 / 2 succeeded");
  });

  it("ignores scan and run events for another run", async () => {
    mount();
    await subscribe();
    await emitScan("UPDATE", { id: 9, run_id: 92, scan_id: 577, status: "written" });
    await emitScan("INSERT", { id: 10, run_id: 92, scan_id: 999, status: "queued" });
    await emitRun({ id: 92, status: "failed", error_message: "other run" });
    expect(screen.getByTestId("table").dataset.count).toBe("2");
    expect(scanEl(577).textContent).toContain("Waiting");
    expect(header().textContent).not.toContain("other run");
  });

  it("counts the header from the held rows, not the run's own counts", async () => {
    run = { ...run, done_count: 0, failed_count: 0 };
    scans = [scanRow(1, 577, { status: "written" }), scanRow(2, 578, { status: "failed", error_message: "boom" })];
    mount();
    await subscribe();
    expect(header().textContent).toContain("Finished · 1 succeeded · 1 failed");
  });

  it("shows elapsed time since creation and the last scan update", async () => {
    scans = [scanRow(1, 577, { updated_at: at(60) }), scanRow(2, 578, { updated_at: at(25 * 60) })];
    mount();
    await subscribe();
    expect(header().textContent).toContain("Requested 30 min ago");
    expect(header().textContent).toContain("Last scan update 5 min ago");

    await emitScan("UPDATE", { id: 1, run_id: 91, scan_id: 577, status: "written", updated_at: at(29 * 60) });
    expect(header().textContent).toContain("Last scan update 1 min ago");
    await tick(10 * 60_000);
    expect(header().textContent).toContain("Requested 40 min ago");
  });

  it("describes empty params as no overrides, and lists any others", async () => {
    mount();
    expect(header().textContent).toContain("Parameters: from each scan's metadata (no overrides)");
    cleanup();
    run = { ...run, params: { age: 7, mode: "cylinder" } };
    mount();
    // Params are inert (bloom#897): shown as requested, never as applied.
    expect(header().textContent).toContain("Requested overrides (not applied yet, bloom#897): age=7, mode=cylinder");
  });

  it("merges a run UPDATE and shows a failed run's error message", async () => {
    mount();
    await subscribe();
    await emitRun({ id: 91, status: "failed", error_message: "dispatch rejected" });
    expect(header().textContent).toContain("dispatch rejected");
  });

  it("links the experiments the run touches and its traits views", async () => {
    mount();
    await subscribe();
    expect(within(header()).getByRole("link", { name: "exp-five" }).getAttribute("href")).toBe("/app/phenotypes/2/5");
    const traits = within(header()).getByRole("link", { name: /Wave 1 · day 14 traits/ });
    expect(traits.getAttribute("href")).toBe("/app/traits/2/5?wave=1&age=14");
    expect(traits.textContent).toBe("Wave 1 · day 14 traits (all scans in the experiment, latest result per scan)");
  });

  it("names and links only the experiments the security-invoker view shows (a soft-deleted one stays hidden)", async () => {
    scans = [scanRow(1, 577), scanRow(2, 578)];
    meta = [scanMeta(577), scanMeta(578, { experiment_id: 6, species_id: 3 })];
    mount();
    await subscribe();
    expect(header().textContent).not.toContain("deleted-exp");
    expect(within(header()).getAllByRole("link", { name: /traits/ }).map((a) => a.getAttribute("href"))).toEqual([
      "/app/traits/2/5?wave=1&age=14",
    ]);
  });

  it("gives a one-scan run one Scan images link and one traits link", async () => {
    run = { ...run, scan_count: 1 };
    scans = [scanRow(1, 577)];
    mount();
    await subscribe();
    expect(screen.getAllByRole("link", { name: "Scan images" }).map((a) => a.getAttribute("href"))).toEqual([
      "/app/phenotypes/2/5/11/7/577",
    ]);
    expect(within(header()).getAllByRole("link", { name: /traits/ })).toHaveLength(1);
  });

  it("shows the timing note", () => {
    mount();
    expect(screen.getByText("Results arrive when each batch of up to 25 scans finishes. Reload the traits page to see new results.")).toBeTruthy();
  });
});

describe("loading", () => {
  it("reads 5000 rows in 6 run-scan requests and hands them all to the table", async () => {
    run = { ...run, scan_count: 5000 };
    scans = Array.from({ length: 5000 }, (_, i) => scanRow(i + 1, 1000 + i));
    meta = [];
    mount();
    await subscribe();
    expect(queriesFor("cyl_pipeline_run_scans")).toHaveLength(6);
    expect(screen.getByTestId("table").dataset.count).toBe("5000");
  });

  it("says no scan rows were recorded when the run has scans but no rows", async () => {
    run = { ...run, scan_count: 40 };
    scans = [];
    mount();
    await subscribe();
    expect(screen.getByText("No scan rows recorded")).toBeTruthy();
  });

  it("says nothing about missing rows before the rows have loaded", () => {
    scans = [];
    mount();
    expect(screen.queryByText("No scan rows recorded")).toBeNull();
    expect(screen.getByText(/Loading scan rows/)).toBeTruthy();
  });
});

describe("failed rows", () => {
  it("explains a stage-in failure, and shows the #900 note only for backstop text on a scan with results", async () => {
    scans = [
      scanRow(1, 577, { status: "failed", error_message: "stage-in failed" }),
      scanRow(2, 578, { status: "failed", error_message: BACKSTOP_MESSAGE }),
      scanRow(3, 579, { status: "failed", error_message: BACKSTOP_MESSAGE }),
      scanRow(4, 580, { status: "failed", error_message: "other" }),
    ];
    run = { ...run, scan_count: 4 };
    meta = [scanMeta(577, { plant_age_days: null }), scanMeta(578), scanMeta(579), scanMeta(580)];
    latest = [
      { scan_id: 578, max_source_id: 40 },
      { scan_id: 579, max_source_id: null },
      { scan_id: 580, max_source_id: 41 },
    ];
    mount();
    await subscribe();
    expect(scanEl(577).textContent).toContain("Likely cause: plant age missing");
    expect(scanEl(578).textContent).toContain(NO_OP_NOTE);
    expect(scanEl(579).textContent).not.toContain("bloom#900");
    expect(scanEl(580).textContent).not.toContain("bloom#900");
  });

  it("batches a burst of rows turning failed into one latest-source read, and reads no metadata it holds", async () => {
    run = { ...run, scan_count: 50 };
    scans = Array.from({ length: 50 }, (_, i) => scanRow(i + 1, 1000 + i));
    meta = scans.map((r) => scanMeta(r.scan_id));
    mount();
    await subscribe();
    const metaReads = queriesFor("cyl_scans_extended").length;
    const latestReads = queriesFor("cyl_scan_latest_source").length;
    // The poller's backstop fails every still-queued row of a workflow in one call.
    for (const r of scans) await emitScan("UPDATE", { ...r, status: "failed", error_message: BACKSTOP_MESSAGE });
    await tick(1000);
    expect(queriesFor("cyl_scans_extended").length).toBe(metaReads);
    const newLatest = queriesFor("cyl_scan_latest_source").slice(latestReads);
    expect(newLatest).toHaveLength(1);
    expect((newLatest[0].arg("in")![1] as number[]).length).toBe(50);
  });

  it("drops a failed-row lookup whose row changed source meanwhile, so unknown never turns into a false no", async () => {
    latest = [];
    mount();
    await subscribe();
    const late = deferred<Answer>();
    supabaseMock.respond = (q) => (q.table === "cyl_scan_latest_source" ? late.promise : respond(q));
    await emitScan("UPDATE", { id: 1, run_id: 91, scan_id: 577, status: "failed", error_message: BACKSTOP_MESSAGE, source_id: null });
    await tick(1000); // the lookup is in flight, reading the old latest
    // A retry's write-back stamps a source; the trigger raised the latest in the same transaction.
    await emitScan("UPDATE", { id: 1, run_id: 91, scan_id: 577, source_id: 60 });
    expect(scanEl(577).textContent).toContain("current=null");
    await act(async () => late.resolve({ data: [], error: null }));
    await tick();
    expect(scanEl(577).textContent).toContain("current=null");
  });

  it("looks a row that turns failed live up exactly once", async () => {
    mount();
    await subscribe();
    const metaCalls = queriesFor("cyl_scans_extended").length;
    const latestCalls = queriesFor("cyl_scan_latest_source").length;
    latest = [{ scan_id: 578, max_source_id: 40 }];

    await emitScan("UPDATE", { id: 2, run_id: 91, scan_id: 578, status: "failed", error_message: BACKSTOP_MESSAGE });
    await tick();
    await emitScan("UPDATE", { id: 2, run_id: 91, scan_id: 578, attempts: 2 });
    await emitScan("UPDATE", { id: 2, run_id: 91, scan_id: 578, status: "failed" });
    await tick(1000); // the batch window
    // Its metadata came with the snapshot, so only the latest source is re-read.
    expect(queriesFor("cyl_scans_extended").length).toBe(metaCalls);
    expect(queriesFor("cyl_scan_latest_source").length).toBe(latestCalls + 1);
    expect(queriesFor("cyl_scan_latest_source").at(-1)!.arg("in")).toEqual(["scan_id", [578]]);
    expect(scanEl(578).textContent).toContain(NO_OP_NOTE);
  });

  it("does no lookup for a row that was already failed in the snapshot", async () => {
    scans = [scanRow(1, 577), scanRow(2, 578, { status: "failed" })];
    mount();
    await subscribe();
    const before = supabaseMock.queries.length;
    await emitScan("UPDATE", { id: 2, run_id: 91, scan_id: 578, status: "failed", attempts: 3 });
    await tick();
    expect(supabaseMock.queries.length).toBe(before);
  });
});

describe("current in trait views", () => {
  it("is yes only when the row's source is the scan's latest", async () => {
    scans = [
      scanRow(1, 577, { status: "written", source_id: 40 }),
      scanRow(2, 578, { status: "written", source_id: 30 }),
      scanRow(3, 579, { status: "queued" }),
    ];
    run = { ...run, scan_count: 3 };
    meta = [scanMeta(577), scanMeta(578), scanMeta(579)];
    latest = [
      { scan_id: 577, max_source_id: 40 },
      { scan_id: 578, max_source_id: 41 },
    ];
    mount();
    await subscribe();
    expect(scanEl(577).textContent).toContain("current=true");
    expect(scanEl(578).textContent).toContain("current=false");
    expect(scanEl(579).textContent).toContain("current=false");
  });

  it("says unknown for a row whose source changed live, without a query", async () => {
    // Can't be inferred: an empty envelope marks a row written without raising the scan's latest source.
    scans = [scanRow(1, 577, { status: "written", source_id: 12 }), scanRow(2, 578)];
    latest = [{ scan_id: 577, max_source_id: 12 }];
    mount();
    await subscribe();
    expect(scanEl(577).textContent).toContain("current=true");
    const before = supabaseMock.queries.length;
    await emitScan("UPDATE", { id: 1, run_id: 91, scan_id: 577, status: "written", source_id: 50 });
    expect(scanEl(577).textContent).toContain("current=null");
    await emitScan("UPDATE", { id: 2, run_id: 91, scan_id: 578, attempts: 1 });
    expect(scanEl(578).textContent).toContain("current=false");
    expect(supabaseMock.queries.length).toBe(before);
  });

  it("says unknown for a row written while the snapshot was being read, and knows again after the next one", async () => {
    mount();
    const pending = deferred<Answer>();
    supabaseMock.respond = (q) => (q.table === "cyl_scan_latest_source" ? pending.promise : respond(q));
    await act(async () => channel().status("SUBSCRIBED"));
    await tick();
    await emitScan("UPDATE", { id: 1, run_id: 91, scan_id: 577, status: "written", source_id: 50 });
    await act(async () => pending.resolve({ data: [], error: null }));
    await tick();
    expect(scanEl(577).textContent).toContain("Result recorded");
    expect(scanEl(577).textContent).toContain("current=null");

    supabaseMock.respond = respond;
    latest = [{ scan_id: 577, max_source_id: 50 }];
    scans = [scanRow(1, 577, { status: "written", source_id: 50 }), scanRow(2, 578)];
    await act(async () => channel().status("TIMED_OUT"));
    await act(async () => fireEvent.click(screen.getByRole("button", { name: /refresh/i })));
    await tick();
    expect(scanEl(577).textContent).toContain("current=true");
  });

  it("shows no rows before the snapshot has loaded", () => {
    mount();
    expect(screen.queryByTestId("scan-577")).toBeNull();
  });
});

describe("sync", () => {
  it("refetches once on the first SUBSCRIBED, and once more at the end of the window after a reconnect", async () => {
    mount();
    expect(queriesFor("cyl_pipeline_run_scans")).toHaveLength(0);
    await subscribe();
    const first = queriesFor("cyl_pipeline_run_scans").length;
    expect(first).toBe(2); // the rows, then the empty page
    await tick(500);
    await act(async () => {
      channel().status("CLOSED");
      channel().status("SUBSCRIBED");
    });
    await tick(1499);
    expect(queriesFor("cyl_pipeline_run_scans")).toHaveLength(first);
    await tick(1);
    expect(queriesFor("cyl_pipeline_run_scans")).toHaveLength(first * 2);
  });

  it("keeps a scan event that arrives during the snapshot fetch", async () => {
    mount();
    const pending = deferred<Answer>();
    supabaseMock.respond = (q) => (q.table === "cyl_pipeline_run_scans" && (q.arg("range") as number[])[0] === 0 ? pending.promise : respond(q));
    await act(async () => channel().status("SUBSCRIBED"));
    await emitScan("UPDATE", { id: 1, run_id: 91, scan_id: 577, status: "written" });
    await act(async () => pending.resolve({ data: [scanRow(1, 577), scanRow(2, 578)], error: null }));
    await tick();
    expect(scanEl(577).textContent).toContain("Result recorded");
  });

  it("keeps a run event that arrives during the snapshot fetch", async () => {
    mount();
    const pending = deferred<Answer>();
    supabaseMock.respond = (q) => (q.table === "cyl_pipeline_runs" ? pending.promise : respond(q));
    await act(async () => channel().status("SUBSCRIBED"));
    await emitRun({ id: 91, status: "failed", error_message: "dispatch rejected" });
    await act(async () => pending.resolve({ data: run, error: null }));
    await tick();
    expect(header().textContent).toContain("dispatch rejected");
  });

  it("shows connecting, live, then offline with a refresh that reloads the rows", async () => {
    mount();
    expect(screen.getByRole("status").textContent).toMatch(/connecting/i);
    await subscribe();
    expect(screen.getByRole("status").textContent).toBe("Live");
    await act(async () => channel().status("TIMED_OUT"));
    expect(screen.getByRole("status").textContent).toMatch(/offline/i);
    const before = queriesFor("cyl_pipeline_run_scans").length;
    await act(async () => fireEvent.click(screen.getByRole("button", { name: /refresh/i })));
    await tick();
    expect(queriesFor("cyl_pipeline_run_scans").length).toBe(before + 2);
  });

  it("shows a failed snapshot's error when the scan rows can't be read", async () => {
    supabaseMock.respond = (q) => (q.table === "cyl_pipeline_run_scans" ? { data: null, error: { message: "timeout" } } : respond(q));
    mount();
    await subscribe();
    expect(screen.getByRole("alert").textContent).toContain("timeout");
  });

  it("keeps the newer snapshot's latest sources when an older snapshot resolves last", async () => {
    scans = [scanRow(1, 577, { status: "written", source_id: 50 }), scanRow(2, 578)];
    mount();
    await subscribe();
    const slow = deferred<Answer>();
    let calls = 0;
    supabaseMock.respond = (q) => {
      if (q.table !== "cyl_scan_latest_source") return respond(q);
      calls += 1;
      return calls === 1 ? slow.promise : { data: [{ scan_id: 577, max_source_id: 50 }], error: null };
    };
    await act(async () => channel().status("TIMED_OUT"));
    await act(async () => fireEvent.click(screen.getAllByRole("button", { name: /refresh/i })[0])); // snapshot A (slow)
    await tick();
    await act(async () => fireEvent.click(screen.getAllByRole("button", { name: /refresh/i })[0])); // snapshot B
    await tick();
    expect(scanEl(577).textContent).toContain("current=true");
    await act(async () => slow.resolve({ data: [{ scan_id: 577, max_source_id: 40 }], error: null }));
    await tick();
    expect(scanEl(577).textContent).toContain("current=true");
  });

  it("shows no traits links, and says why, when the experiments view can't be read", async () => {
    supabaseMock.respond = (q) => (q.table === "cyl_pipeline_run_experiments" ? { data: null, error: { message: "view missing" } } : respond(q));
    mount();
    await subscribe();
    expect(within(header()).queryAllByRole("link", { name: /traits/ })).toHaveLength(0);
    expect(screen.getByText(/view missing/)).toBeTruthy();
    expect(screen.getByTestId("table").dataset.count).toBe("2");
  });

  it("still shows the rows when scan details or latest sources can't be read", async () => {
    supabaseMock.respond = (q) =>
      q.table === "cyl_scan_latest_source" || q.table === "cyl_scans_extended" ? { data: null, error: { message: "timeout" } } : respond(q);
    mount();
    await subscribe();
    expect(screen.getByTestId("table").dataset.count).toBe("2");
    expect(scanEl(577).textContent).toContain("current=null");
    expect(screen.getByText(/Scan details unavailable \(scan details: timeout; latest sources: timeout\)/)).toBeTruthy();
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("says live updates are unavailable, not loading, when the channel fails before the rows load, and Refresh loads them", async () => {
    mount();
    await act(async () => channel().status("CHANNEL_ERROR"));
    expect(screen.queryByText(/Loading scan rows/)).toBeNull();
    expect(screen.getByText(/Live updates are unavailable/)).toBeTruthy();
    const notice = screen.getByText(/Live updates are unavailable/);
    await act(async () => fireEvent.click(within(notice).getByRole("button", { name: "Refresh" })));
    await tick();
    expect(screen.getByTestId("table").dataset.count).toBe("2");
  });

  it("hands the ?status filter to the table", async () => {
    mount(false, "failed");
    await subscribe();
    expect(screen.getByTestId("table").dataset.filter).toBe("failed");
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

describe("re-run actions", () => {
  const rows = (spec: [number, string][]) => spec.map(([scan_id, status], i) => scanRow(i + 1, scan_id, { status }));
  const rerunFailed = () => screen.queryByRole("button", { name: /^Re-run failed scans/ }) as HTMLButtonElement | null;
  const rerunUnresulted = () => screen.queryByRole("button", { name: /^Re-run scans without a result/ }) as HTMLButtonElement | null;

  it("hides Re-run failed until the header counts settle", async () => {
    run = { ...run, scan_count: 2, status: "running" };
    scans = rows([
      [577, "queued"],
      [578, "failed"],
    ]);
    mount();
    expect(rerunFailed()).toBeNull();
    await subscribe();
    expect(rerunFailed()).toBeNull();
    await emitScan("UPDATE", { id: 1, run_id: 91, scan_id: 577, status: "written" });
    expect(rerunFailed()!.textContent).toBe("Re-run failed scans (1)");
  });

  it("submits exactly the failed scans once the counts settle", async () => {
    run = { ...run, scan_count: 3, status: "running" };
    scans = rows([
      [577, "written"],
      [578, "failed"],
      [579, "failed"],
    ]);
    mount();
    await subscribe();
    fireEvent.click(rerunFailed()!);
    expect(dialog.props!.target).toEqual({ target_level: "scan_ids", scan_ids: [578, 579] });
    expect(screen.queryByText(/bloom#900/)).toBeNull();
  });

  it("warns when a failed row carries the #900 note", async () => {
    run = { ...run, scan_count: 2, status: "complete" };
    scans = [scanRow(1, 577, { status: "written", source_id: 5 }), scanRow(2, 578, { status: "failed", error_message: BACKSTOP_MESSAGE })];
    latest = [
      { scan_id: 577, max_source_id: 5 },
      { scan_id: 578, max_source_id: 7 },
    ];
    mount();
    await subscribe();
    expect(rerunFailed()!.textContent).toBe("Re-run failed scans (1)");
    expect(screen.getByTestId("rerun-actions").textContent).toContain(
      "Some of these scans already have pipeline results this run didn't record (bloom#900); re-running won't change them. Check their traits before re-running.",
    );
  });

  it.each(["complete", "failed"])("offers Re-run scans without a result on a %s run with unresulted scans", async (status) => {
    run = { ...run, scan_count: 40, status };
    scans = [
      ...Array.from({ length: 30 }, (_, i) => scanRow(i + 1, 1000 + i, { status: "written" })),
      ...Array.from({ length: 2 }, (_, i) => scanRow(31 + i, 2000 + i, { status: "failed" })),
      ...Array.from({ length: 8 }, (_, i) => scanRow(33 + i, 3000 + i, { status: "queued" })),
    ];
    mount();
    await subscribe();
    expect(rerunUnresulted()!.textContent).toBe("Re-run scans without a result (10)");
    expect(screen.getByTestId("rerun-actions").textContent).toContain(
      "Scans still processing on the cluster could be processed twice.",
    );
    expect(rerunFailed()).toBeNull();
    fireEvent.click(rerunUnresulted()!);
    const ids = (dialog.props!.target as { scan_ids: number[] }).scan_ids;
    expect([...ids].sort((a, b) => a - b)).toEqual([2000, 2001, 3000, 3001, 3002, 3003, 3004, 3005, 3006, 3007]);
  });

  it.each(["running", "partial", "queued", "submitted"])("offers no retry of unresulted scans on a %s run", async (status) => {
    run = { ...run, scan_count: 3, status };
    scans = rows([
      [577, "written"],
      [578, "failed"],
      [579, "queued"],
    ]);
    mount();
    await subscribe();
    expect(rerunUnresulted()).toBeNull();
    expect(rerunFailed()).toBeNull();
  });

  it("offers only Re-run failed once a complete run's counts settle", async () => {
    run = { ...run, scan_count: 40, status: "complete" };
    scans = [
      ...Array.from({ length: 38 }, (_, i) => scanRow(i + 1, 1000 + i, { status: "written" })),
      ...Array.from({ length: 2 }, (_, i) => scanRow(39 + i, 2000 + i, { status: "failed" })),
    ];
    mount();
    await subscribe();
    expect(rerunFailed()!.textContent).toBe("Re-run failed scans (2)");
    expect(rerunUnresulted()).toBeNull();
  });

  it("offers nothing on a run with no failures", async () => {
    run = { ...run, scan_count: 2, status: "complete" };
    scans = rows([
      [577, "written"],
      [578, "written"],
    ]);
    mount();
    await subscribe();
    expect(screen.queryByTestId("rerun-actions")).toBeNull();
  });

  it("disables both over MAX_TRIGGER_SCAN_IDS", async () => {
    run = { ...run, scan_count: 5002, status: "failed" };
    scans = [
      ...Array.from({ length: 5001 }, (_, i) => scanRow(i + 1, i + 1, { status: "failed" })),
      scanRow(5002, 5002, { status: "queued" }),
    ];
    mount();
    await subscribe();
    expect(rerunUnresulted()!.textContent).toBe("Re-run scans without a result (5002)");
    expect(rerunUnresulted()!.disabled).toBe(true);

    run = { ...run, scan_count: 5001, status: "complete" };
    scans = Array.from({ length: 5001 }, (_, i) => scanRow(i + 1, i + 1, { status: "failed" }));
    cleanup();
    resetSupabaseMock(respond);
    mount();
    await subscribe();
    expect(rerunFailed()!.textContent).toBe("Re-run failed scans (5001)");
    expect(rerunFailed()!.disabled).toBe(true);
  });

  it("keeps an open re-run dialog when a live event withdraws its action", async () => {
    run = { ...run, scan_count: 2, status: "complete" };
    scans = rows([
      [577, "queued"],
      [578, "failed"],
    ]);
    mount();
    await subscribe();
    fireEvent.click(rerunUnresulted()!);
    expect(screen.getByRole("dialog")).toBeTruthy();
    await emitScan("UPDATE", { id: 1, run_id: 91, scan_id: 577, status: "written" });
    // The counts settled: the action is withdrawn and Re-run failed offered instead, but the open dialog stays.
    expect(rerunUnresulted()).toBeNull();
    expect(rerunFailed()!.textContent).toBe("Re-run failed scans (1)");
    expect(screen.getByRole("dialog")).toBeTruthy();
    expect(dialog.props!.target).toEqual({ target_level: "scan_ids", scan_ids: [577, 578] });
  });
});
