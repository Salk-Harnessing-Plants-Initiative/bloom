/**
 * The live views' pure state: buffered replay, merge, monotonic counts, the
 * list's keyset window, and the drill-down's per-run filtering. Payloads come
 * from task 0.3's real Realtime captures.
 */

import { describe, expect, it } from "vitest";
import runCapture from "./__fixtures__/realtime-runs.json";
import scanCapture from "./__fixtures__/realtime-run-scans.json";
import {
  addPanelRun,
  appendOlder,
  applyDetailChange,
  applyListChange,
  applyPanelChange,
  beginFetch,
  countsFromScanRows,
  detailFromSnapshot,
  endFetch,
  failFetch,
  listFromSnapshot,
  mergeRun,
  mergeScanRow,
  receive,
  synced,
  type Change,
  type RunList,
  type RunRow,
  type RunScanRow,
} from "./realtime-reducer";

type Captured = { step: string; payload: { table: string; eventType: string; new: Record<string, unknown> } };
const runEvents = runCapture.events as unknown as Captured[];
const scanEvents = scanCapture.events as unknown as Captured[];
const captured = (events: Captured[], step: string) => {
  const found = events.find((e) => e.step === step);
  if (!found) throw new Error(`no captured ${step}`);
  return found.payload as unknown as Change<RunRow> & Change<RunScanRow>;
};

function run(id: number, created_at: string, overrides: Partial<RunRow> = {}): RunRow {
  return {
    id,
    created_at,
    completed_at: null,
    done_count: 0,
    error_message: null,
    failed_count: 0,
    params: {},
    requested_by: "4965b3af-ccfe-40f5-814b-447e1f726e1b",
    reused_count: 0,
    scan_count: 40,
    status: "running",
    submitted_at: null,
    target_id: null,
    target_level: "scan_ids",
    ...overrides,
  };
}

const at = (second: number, fraction = "") => `2026-09-28T10:00:${String(second).padStart(2, "0")}${fraction}+00:00`;
const update = (row: Partial<RunRow>): Change<RunRow> => ({ table: "cyl_pipeline_runs", eventType: "UPDATE", new: row });
const insert = (row: Partial<RunRow>): Change<RunRow> => ({ table: "cyl_pipeline_runs", eventType: "INSERT", new: row });
const ids = (list: RunList) => list.rows.map((r) => r.id);

describe("merge", () => {
  it("keeps a field the payload omits (the captured TOAST case)", () => {
    const withError = captured(runEvents, "update-run-toast-error").new as RunRow;
    const doneOnly = captured(runEvents, "update-run-done-count-only").new as Partial<RunRow>;
    expect(withError.error_message).toHaveLength(6400);
    expect("error_message" in doneOnly).toBe(false);

    const merged = mergeRun(withError, doneOnly);
    expect(merged.error_message).toBe(withError.error_message);
    expect(merged.done_count).toBe(1);
  });

  it("keeps an omitted field on a run-scan row too", () => {
    const withError = captured(scanEvents, "update-scan-toast-error").new as RunScanRow;
    const attemptsOnly = captured(scanEvents, "update-scan-attempts-only").new as Partial<RunScanRow>;
    expect("error_message" in attemptsOnly).toBe(false);

    const merged = mergeScanRow(withError, attemptsOnly);
    expect(merged.error_message).toBe(withError.error_message);
    expect(merged.attempts).toBe(1);
  });

  it("never lets a held run's counts decrease", () => {
    const held = run(91, at(1), { done_count: 12, failed_count: 3 });
    const merged = mergeRun(held, { id: 91, done_count: 11, failed_count: 2, status: "running" });
    expect(merged.done_count).toBe(12);
    expect(merged.failed_count).toBe(3);
    expect(mergeRun(held, { id: 91, done_count: 13 }).done_count).toBe(13);
  });

  it("takes an unheld row as it comes", () => {
    const row = captured(runEvents, "insert-run").new as RunRow;
    expect(mergeRun(undefined, row)).toEqual(row);
  });
});

describe("buffered sync", () => {
  const apply = (list: RunList, change: Change<RunRow>) => applyListChange(list, change);

  it("replays an event that arrived during the fetch on top of the snapshot", () => {
    let s = synced<RunList, Change<RunRow>>(listFromSnapshot([run(91, at(1), { done_count: 12 })]));
    s = beginFetch(s);
    s = receive(s, update({ id: 91, done_count: 13 }), apply);
    expect(s.view.rows[0].done_count).toBe(12);
    expect(s.buffer).toHaveLength(1);

    s = endFetch(s, listFromSnapshot([run(91, at(1), { done_count: 12 })]), apply);
    expect(s.view.rows[0].done_count).toBe(13);
    expect(s.fetching).toBe(false);
    expect(s.buffer).toHaveLength(0);
  });

  it("applies events directly when no fetch is in flight", () => {
    let s = synced<RunList, Change<RunRow>>(listFromSnapshot([run(91, at(1))]));
    s = receive(s, update({ id: 91, done_count: 5 }), apply);
    expect(s.view.rows[0].done_count).toBe(5);
    expect(s.buffer).toHaveLength(0);
  });

  it("keeps the buffer across overlapping fetches until one ends", () => {
    let s = synced<RunList, Change<RunRow>>(listFromSnapshot([run(91, at(1))]));
    s = beginFetch(s);
    s = receive(s, update({ id: 91, done_count: 5 }), apply);
    s = beginFetch(s);
    expect(s.buffer).toHaveLength(1);
    s = endFetch(s, listFromSnapshot([run(91, at(1))]), apply);
    expect(s.view.rows[0].done_count).toBe(5);
  });

  it("replays the buffer onto the held view when the fetch fails", () => {
    let s = synced<RunList, Change<RunRow>>(listFromSnapshot([run(91, at(1))]));
    s = beginFetch(s);
    s = receive(s, update({ id: 91, done_count: 7 }), apply);
    s = failFetch(s, apply);
    expect(s.view.rows[0].done_count).toBe(7);
    expect(s.fetching).toBe(false);
  });

  it("replays captured events in order onto an empty snapshot", () => {
    const events = runEvents.filter((e) => e.payload.eventType !== "DELETE").map((e) => e.payload as unknown as Change<RunRow>);
    let s = beginFetch(synced<RunList, Change<RunRow>>(listFromSnapshot([])));
    for (const e of events) s = receive(s, e, apply);
    s = endFetch(s, listFromSnapshot([]), apply);
    expect(s.view.rows).toHaveLength(1);
    const row = s.view.rows[0];
    expect(row.status).toBe("failed");
    expect(row.done_count).toBe(1);
    expect(row.error_message).toHaveLength(6400);
  });
});

describe("the runs list window", () => {
  const fifty = Array.from({ length: 50 }, (_, i) => run(200 - i, at(59 - i)));

  it("orders by created_at then id, both descending", () => {
    const list = listFromSnapshot([run(1, at(1)), run(3, at(2)), run(2, at(2))]);
    expect(ids(list)).toEqual([3, 2, 1]);
  });

  it("breaks created_at ties by id, at microsecond precision", () => {
    const list = listFromSnapshot([run(7, at(2, ".1234")), run(9, at(2, ".123400")), run(8, at(2, ".123401"))]);
    expect(ids(list)).toEqual([8, 9, 7]);
  });

  it("puts its cursor on the oldest snapshot row, and knows when there may be older rows", () => {
    const list = listFromSnapshot(fifty);
    expect(list.cursor).toEqual({ created_at: at(10), id: 151 });
    expect(list.hasOlder).toBe(true);
    expect(listFromSnapshot(fifty.slice(0, 3)).hasOlder).toBe(false);
  });

  it("merges an update for a held run without moving it", () => {
    const list = applyListChange(listFromSnapshot(fifty), update({ id: 180, done_count: 13, created_at: at(39) }));
    expect(list.rows.find((r) => r.id === 180)?.done_count).toBe(13);
    expect(list.rows).toHaveLength(50);
  });

  it("puts a new run created after every loaded run on top", () => {
    const list = applyListChange(listFromSnapshot(fifty), insert(run(201, "2026-09-28T10:01:00+00:00", { status: "queued" })));
    expect(ids(list)[0]).toBe(201);
    expect(list.rows).toHaveLength(51);
  });

  it("ignores an unknown run older than the oldest loaded row", () => {
    const before = listFromSnapshot(fifty);
    const after = applyListChange(before, update(run(100, at(5))));
    expect(ids(after)).toEqual(ids(before));
  });

  it("inserts an unknown run that sorts at or after the oldest loaded row", () => {
    // Same created_at as the cursor row, higher id: sorts after it.
    const list = applyListChange(listFromSnapshot(fifty), update(run(300, at(10))));
    expect(ids(list)).toContain(300);
    expect(ids(list).indexOf(300)).toBe(ids(list).indexOf(151) - 1);
    // Same created_at, lower id: sorts before the cursor, so it stays out.
    expect(ids(applyListChange(listFromSnapshot(fifty), update(run(150, at(10)))))).not.toContain(150);
  });

  it("inserts every event when no row is loaded", () => {
    let list = listFromSnapshot([]);
    list = applyListChange(list, update(run(5, at(1))));
    list = applyListChange(list, update(run(4, "2020-01-01T00:00:00+00:00")));
    expect(ids(list)).toEqual([5, 4]);
    expect(list.cursor).toBeNull();
  });

  it("moves the cursor only on a snapshot or a load of older rows", () => {
    let list = listFromSnapshot(fifty);
    const cursor = list.cursor;
    list = applyListChange(list, insert(run(201, "2026-09-28T10:01:00+00:00")));
    list = applyListChange(list, update({ id: 151, done_count: 3 }));
    expect(list.cursor).toEqual(cursor);

    list = appendOlder(list, [run(150, at(9)), run(149, at(8))]);
    expect(list.cursor).toEqual({ created_at: at(8), id: 149 });
  });

  it("returns a run skipped by the window on the next load of older rows, in order, without duplicates", () => {
    let list = listFromSnapshot(fifty);
    list = applyListChange(list, update(run(100, at(5), { done_count: 2 })));
    expect(ids(list)).not.toContain(100);

    // The keyset query returns rows strictly older than the cursor; one overlaps a held row.
    list = appendOlder(list, [run(151, at(10)), run(120, at(7)), run(100, at(5)), run(99, at(4))]);
    const all = ids(list);
    expect(new Set(all).size).toBe(all.length);
    expect(all.slice(-3)).toEqual([120, 100, 99]);
    expect(list.hasOlder).toBe(false);
  });

  it("de-duplicates a load of older rows by id, keeping the held row", () => {
    let list = listFromSnapshot([run(10, at(10), { done_count: 9 })]);
    list = appendOlder(list, [run(10, at(10), { done_count: 1 }), run(9, at(9))]);
    expect(ids(list)).toEqual([10, 9]);
    expect(list.rows[0].done_count).toBe(9);
  });

  it("keeps the cursor when a load of older rows returns nothing", () => {
    const list = appendOlder(listFromSnapshot(fifty), []);
    expect(list.cursor).toEqual({ created_at: at(10), id: 151 });
    expect(list.hasOlder).toBe(false);
  });

  it("drops an unheld event the filter rejects, and still merges held rows", () => {
    const mine = (row: Partial<RunRow>) => row.requested_by === "me";
    let list = listFromSnapshot([run(10, at(10), { requested_by: "me" })]);
    list = applyListChange(list, insert(run(11, at(11), { requested_by: "someone" })), mine);
    list = applyListChange(list, update({ id: 10, done_count: 4 }), mine);
    expect(ids(list)).toEqual([10]);
    expect(list.rows[0].done_count).toBe(4);
  });

  it("ignores events from other tables and deletes", () => {
    const list = listFromSnapshot([run(10, at(10))]);
    expect(applyListChange(list, { table: "cyl_pipeline_run_scans", eventType: "UPDATE", new: { id: 10, done_count: 3 } } as Change<RunRow>)).toEqual(list);
    expect(applyListChange(list, { table: "cyl_pipeline_runs", eventType: "DELETE", new: {} })).toEqual(list);
  });
});

describe("the drill-down", () => {
  const scan = (id: number, scan_id: number, overrides: Partial<RunScanRow> = {}): RunScanRow => ({
    id,
    run_id: 91,
    scan_id,
    status: "queued",
    attempts: 0,
    argo_workflow_name: null,
    batch_index: null,
    created_at: at(1),
    updated_at: at(1),
    error_message: null,
    source_id: null,
    ...overrides,
  });
  const scanChange = (row: Partial<RunScanRow>): Change<RunScanRow> => ({ table: "cyl_pipeline_run_scans", eventType: "UPDATE", new: row });

  it("applies a scan update for its run, and counts it", () => {
    let detail = detailFromSnapshot(run(91, at(1)), [scan(1, 577), scan(2, 578)]);
    detail = applyDetailChange(detail, scanChange({ id: 1, run_id: 91, scan_id: 577, status: "written" }), 91);
    expect(detail.scans.get(1)?.status).toBe("written");
    expect(countsFromScanRows(detail.scans.values())).toEqual({ done: 1, failed: 0 });
  });

  it("ignores scan and run events for another run", () => {
    const detail = detailFromSnapshot(run(91, at(1)), [scan(1, 577)]);
    const other = applyDetailChange(detail, scanChange({ id: 9, run_id: 92, scan_id: 577, status: "written" }), 91);
    expect([...other.scans.keys()]).toEqual([1]);
    const otherRun = applyDetailChange(detail, update({ id: 92, status: "failed" }), 91);
    expect(otherRun.run.status).toBe("running");
  });

  it("merges run updates with monotonic counts", () => {
    let detail = detailFromSnapshot(run(91, at(1), { done_count: 4 }), []);
    detail = applyDetailChange(detail, update({ id: 91, done_count: 3, status: "partial" }), 91);
    expect(detail.run.done_count).toBe(4);
    expect(detail.run.status).toBe("partial");
  });

  it("adds a scan row it did not hold, and keeps TOASTed fields on the next update", () => {
    const toast = captured(scanEvents, "update-scan-toast-error").new as RunScanRow;
    const attempts = captured(scanEvents, "update-scan-attempts-only").new as Partial<RunScanRow>;
    let detail = detailFromSnapshot(run(toast.run_id, at(1)), []);
    detail = applyDetailChange(detail, scanChange(toast), toast.run_id);
    detail = applyDetailChange(detail, scanChange(attempts), toast.run_id);
    expect(detail.scans.get(toast.id)?.error_message).toHaveLength(6400);
    expect(detail.scans.get(toast.id)?.attempts).toBe(1);
  });
});

describe("countsFromScanRows", () => {
  it("counts written and reused as done, and failed as failed", () => {
    const rows = ["queued", "predicted", "written", "reused", "failed", "failed", "mystery"].map((status) => ({ status }));
    expect(countsFromScanRows(rows)).toEqual({ done: 2, failed: 2 });
  });

  it("counts nothing for no rows", () => {
    expect(countsFromScanRows([])).toEqual({ done: 0, failed: 0 });
  });
});

describe("the experiment panel's runs", () => {
  const ten = Array.from({ length: 10 }, (_, i) => run(50 - i, at(30 - i)));

  it("keeps only the 10 most recent when a run is added", () => {
    const runs = addPanelRun(ten, run(60, at(40)));
    expect(runs).toHaveLength(10);
    expect(runs[0].id).toBe(60);
    expect(runs.map((r) => r.id)).not.toContain(41);
  });

  it("drops an added run older than all ten", () => {
    expect(addPanelRun(ten, run(1, at(1))).map((r) => r.id)).toEqual(ten.map((r) => r.id));
  });

  it("merges an added run it already holds", () => {
    const runs = addPanelRun(ten, run(50, at(30), { done_count: 3 }));
    expect(runs).toHaveLength(10);
    expect(runs[0].done_count).toBe(3);
  });

  it("applies an event for a held run, and reports whether it was held", () => {
    const held = applyPanelChange(ten, update({ id: 45, done_count: 2 }));
    expect(held.held).toBe(true);
    expect(held.runs.find((r) => r.id === 45)?.done_count).toBe(2);
    const unheld = applyPanelChange(ten, update({ id: 99, done_count: 2 }));
    expect(unheld.held).toBe(false);
    expect(unheld.runs).toBe(ten);
  });
});
