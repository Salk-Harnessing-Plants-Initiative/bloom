/** The Video Jobs list: merging both kinds, live changes, queue place, slowness and links. */

import { describe, expect, it } from "vitest";
import {
  JOB_PAGE_SIZE,
  PLATE_JOBS_TABLE,
  RENDERING_QUIET_MS,
  SCAN_JOBS_TABLE,
  UNCLAIMED_LONG_MS,
  appendOlder,
  applyActiveChange,
  applyJobChange,
  jobHref,
  listFromSnapshot,
  mergePages,
  overdue,
  queuedAhead,
  waveKey,
  type ActiveJob,
  type ListedJob,
  type PlateJob,
  type ScanJob,
} from "./list";

const at = (minute: number) => `2026-10-29T10:${String(minute).padStart(2, "0")}:00+00:00`;

function plate(id: string, minute: number, over: Partial<PlateJob> = {}): PlateJob {
  return {
    kind: "plate",
    id,
    experiment_id: 12,
    plate_id: "P7",
    wave_number: 1,
    status: "queued",
    stage: null,
    frames_done: null,
    frames_total: null,
    error_code: null,
    requested_by: null,
    created_at: at(minute),
    started_at: null,
    updated_at: at(minute),
    completed_at: null,
    ...over,
  };
}

function scan(id: string, minute: number, over: Partial<ScanJob> = {}): ScanJob {
  return {
    kind: "scan",
    id,
    experiment_id: 3,
    scan_id: 44,
    status: "queued",
    stage: null,
    frames_done: null,
    frames_total: null,
    error_code: null,
    requested_by: null,
    created_at: at(minute),
    started_at: null,
    updated_at: at(minute),
    completed_at: null,
    ...over,
  };
}

const change = (table: string, eventType: string, row: Record<string, unknown>) => ({
  table,
  eventType,
  new: row,
});

describe("one list for both kinds", () => {
  it("merges plates and scans newest first", () => {
    const { rows } = mergePages([plate("p1", 1), plate("p3", 3)], [scan("s2", 2)]);
    expect(rows.map((r) => r.id)).toEqual(["p3", "s2", "p1"]);
  });

  it("keeps one page and says more are older when the two together overflow it", () => {
    const plates = Array.from({ length: 30 }, (_, i) => plate(`p${i}`, i));
    const scans = Array.from({ length: 30 }, (_, i) => scan(`s${i}`, i));
    const merged = mergePages(plates, scans);
    expect(merged.rows).toHaveLength(JOB_PAGE_SIZE);
    expect(merged.hasOlder).toBe(true);
  });

  it("says more are older when one table alone filled its page", () => {
    const plates = Array.from({ length: JOB_PAGE_SIZE }, (_, i) => plate(`p${i}`, i % 60));
    expect(mergePages(plates, []).hasOlder).toBe(true);
  });

  it("says nothing is older when the two together fill exactly one page", () => {
    const half = JOB_PAGE_SIZE / 2;
    const plates = Array.from({ length: half }, (_, i) => plate(`p${i}`, i));
    const scans = Array.from({ length: half }, (_, i) => scan(`s${i}`, i));
    expect(mergePages(plates, scans).hasOlder).toBe(false);
  });

  it("says nothing is older when both tables returned less than a page", () => {
    expect(mergePages([plate("p1", 1)], [scan("s1", 2)]).hasOlder).toBe(false);
  });

  it("appends an older page without repeating the row it shares with the cursor", () => {
    const list = listFromSnapshot([plate("p5", 5), scan("s4", 4)], true);
    const next = appendOlder(list, [scan("s4", 4), plate("p2", 2)], false);
    expect(next.rows.map((r) => r.id)).toEqual(["p5", "s4", "p2"]);
    expect(next.cursor).toBe(at(2));
    expect(next.hasOlder).toBe(false);
  });
});

describe("live changes", () => {
  it("updates a held job from either table in place", () => {
    const list = listFromSnapshot([plate("j1", 1), scan("j2", 2)], false);
    const next = applyJobChange(
      list,
      change(SCAN_JOBS_TABLE, "UPDATE", { id: "j2", status: "rendering", stage: "downloading" })
    );
    const held = next.rows.find((r) => r.id === "j2")!;
    expect(held.status).toBe("rendering");
    expect(held.kind).toBe("scan");
    expect(held.experiment_id).toBe(3);
  });

  it("does not confuse a plate job and a scan job that share an id", () => {
    const list = listFromSnapshot([plate("same", 1), scan("same", 2)], false);
    const next = applyJobChange(list, change(PLATE_JOBS_TABLE, "UPDATE", { id: "same", status: "failed" }));
    expect(next.rows.find((r) => r.kind === "plate")!.status).toBe("failed");
    expect(next.rows.find((r) => r.kind === "scan")!.status).toBe("queued");
  });

  it("adds a new job at the top", () => {
    const list = listFromSnapshot([plate("old", 1)], false);
    const fresh = { ...scan("new", 9), kind: undefined };
    const next = applyJobChange(list, change(SCAN_JOBS_TABLE, "INSERT", fresh));
    expect(next.rows.map((r) => r.id)).toEqual(["new", "old"]);
  });

  it("leaves out a new job the filter rejects", () => {
    const list = listFromSnapshot([plate("old", 1)], false);
    const next = applyJobChange(list, change(SCAN_JOBS_TABLE, "INSERT", scan("new", 9)), (j) => j.kind === "plate");
    expect(next.rows.map((r) => r.id)).toEqual(["old"]);
  });

  it("leaves out a new job older than the loaded range, for load older to bring", () => {
    const list = listFromSnapshot([plate("p5", 5), plate("p4", 4)], true);
    const next = applyJobChange(list, change(PLATE_JOBS_TABLE, "INSERT", plate("p1", 1)));
    expect(next).toBe(list);
  });

  it.each([
    ["an unknown status", { status: "paused" }],
    ["no experiment", { experiment_id: null }],
    ["no plate id", { plate_id: null }],
    ["no updated_at", { updated_at: undefined }],
  ])("drops a payload with %s rather than show a blank or broken row", (_what, bad) => {
    const list = listFromSnapshot([], false);
    const next = applyJobChange(list, change(PLATE_JOBS_TABLE, "INSERT", { ...plate("j1", 1), ...bad }));
    expect(next.rows).toEqual([]);
  });

  it("ignores another table's changes", () => {
    const list = listFromSnapshot([plate("j1", 1)], false);
    expect(applyJobChange(list, change("cyl_pipeline_runs", "INSERT", { id: 5 }))).toBe(list);
  });

  it("never carries a column the page did not ask for", () => {
    const list = listFromSnapshot([], false);
    const next = applyJobChange(
      list,
      change(SCAN_JOBS_TABLE, "INSERT", { ...scan("j1", 1), error_message: "kong:8000" })
    );
    expect(Object.keys(next.rows[0])).not.toContain("error_message");
  });
});

const active = (kind: "plate" | "scan", id: string, minute: number, status = "queued", updatedMinute = minute): ActiveJob => ({
  kind,
  id,
  status,
  created_at: at(minute),
  updated_at: at(updatedMinute),
});

describe("queue place", () => {
  it("counts the job rendering and those queued earlier, of the same kind, loaded or not", () => {
    const line = [active("plate", "p0", 0), active("plate", "p1", 1), active("plate", "p2", 2, "rendering"), active("scan", "s2", 2)];
    expect(queuedAhead(line, plate("p3", 3))).toBe(3);
    expect(queuedAhead(line, plate("p1", 1))).toBe(2);
    expect(queuedAhead(line, scan("s3", 3))).toBe(1);
  });

  it("has no place once the job is rendering", () => {
    expect(queuedAhead([], plate("p1", 1, { status: "rendering" }))).toBeNull();
  });

  it("follows the line as jobs are claimed and finished", () => {
    let line: ActiveJob[] = [];
    line = applyActiveChange(line, change(PLATE_JOBS_TABLE, "INSERT", plate("p1", 1)));
    line = applyActiveChange(line, change(PLATE_JOBS_TABLE, "INSERT", plate("p2", 2)));
    expect(queuedAhead(line, plate("p2", 2))).toBe(1);
    line = applyActiveChange(line, change(PLATE_JOBS_TABLE, "UPDATE", { id: "p1", status: "rendering" }));
    expect(queuedAhead(line, plate("p2", 2))).toBe(1);
    line = applyActiveChange(line, change(PLATE_JOBS_TABLE, "UPDATE", { id: "p1", status: "rendered" }));
    expect(line.map((a) => a.id)).toEqual(["p2"]);
    expect(queuedAhead(line, plate("p2", 2))).toBe(0);
  });
});

describe("slow jobs", () => {
  const now = Date.parse(at(30));
  const ago = (ms: number) => new Date(now - ms).toISOString();

  it("flags a rendering job that has not reported for longer than the worker renews it", () => {
    expect(overdue(plate("p", 0, { status: "rendering", updated_at: ago(RENDERING_QUIET_MS + 1) }), now, [])).toBe("quiet");
    expect(overdue(plate("p", 0, { status: "rendering", updated_at: ago(RENDERING_QUIET_MS) }), now, [])).toBeNull();
  });

  it("flags a job no worker has claimed when nothing of its kind is rendering", () => {
    const waiting = scan("s", 0, { created_at: ago(UNCLAIMED_LONG_MS + 1) });
    expect(overdue(waiting, now, [])).toBe("waiting");
    expect(overdue(scan("s", 0, { created_at: ago(UNCLAIMED_LONG_MS) }), now, [])).toBeNull();
  });

  it("does not flag a job waiting its turn behind a render that is moving", () => {
    const waiting = plate("p", 0, { created_at: ago(UNCLAIMED_LONG_MS * 10) });
    const busy: ActiveJob = { kind: "plate", id: "r", status: "rendering", created_at: ago(UNCLAIMED_LONG_MS * 11), updated_at: ago(1000) };
    expect(overdue(waiting, now, [busy])).toBeNull();
    // A scan rendering says nothing about the plate worker.
    expect(overdue(waiting, now, [{ ...busy, kind: "scan" }])).toBe("waiting");
  });

  it("flags the wait again when the render ahead has gone quiet", () => {
    const waiting = plate("p", 0, { created_at: ago(UNCLAIMED_LONG_MS * 10) });
    const stuck: ActiveJob = { kind: "plate", id: "r", status: "rendering", created_at: ago(UNCLAIMED_LONG_MS * 11), updated_at: ago(RENDERING_QUIET_MS + 1) };
    expect(overdue(waiting, now, [stuck])).toBe("waiting");
  });

  it("never flags a finished job", () => {
    expect(overdue(plate("p", 0, { status: "rendered" }), now, [])).toBeNull();
  });
});

describe("the page each video plays on", () => {
  const plates = new Map([[12, { experiment_id: 12, name: "Grav-12", species_id: 2 }]]);
  const scans = new Map([
    [44, { scan_id: 44, experiment_name: "Cyl-3", species_id: 1, wave_id: 7, accession_id: 9, plant_age_days: 5 }],
  ]);

  it("links a plate to its plate page, wave and all", () => {
    expect(jobHref(plate("p", 0), plates, scans)).toBe("/app/plate-phenotypes/2/12/wave/1/P7");
  });

  it("links a plate with no wave to its 'none' wave", () => {
    expect(jobHref(plate("p", 0, { wave_number: null }), plates, scans)).toBe(
      "/app/plate-phenotypes/2/12/wave/none/P7"
    );
    expect(waveKey(0)).toBe("0");
  });

  it("escapes a plate id in the address", () => {
    expect(jobHref(plate("p", 0, { plate_id: "Plate 1#a" }), plates, scans)).toBe(
      "/app/plate-phenotypes/2/12/wave/1/Plate%201%23a"
    );
  });

  it("links a cylinder scan to its scan page", () => {
    expect(jobHref(scan("s", 0), plates, scans)).toBe("/app/phenotypes/1/3/7/9/44");
  });

  it("has no link until the lookup has every part", () => {
    expect(jobHref(scan("s", 0, { scan_id: 99 }), plates, scans)).toBeNull();
    expect(jobHref(plate("p", 0, { experiment_id: 99 }), plates, scans)).toBeNull();
    const partial = new Map([[44, { ...scans.get(44)!, wave_id: null }]]);
    expect(jobHref(scan("s", 0), plates, partial)).toBeNull();
    const noAccession = new Map([[44, { ...scans.get(44)!, accession_id: null }]]);
    expect(jobHref(scan("s", 0), plates, noAccession)).toBeNull();
    const hidden = new Map([[12, { experiment_id: 12, name: null, species_id: null }]]);
    expect(jobHref(plate("p", 0), hidden, scans)).toBeNull();
  });
});
