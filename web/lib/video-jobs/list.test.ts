/** The Video Jobs list: merging both kinds, live changes, queue place, slowness and links. */

import { describe, expect, it } from "vitest";
import {
  JOB_PAGE_SIZE,
  PLATE_JOBS_TABLE,
  QUEUED_LONG_MS,
  RENDERING_QUIET_MS,
  SCAN_JOBS_TABLE,
  appendOlder,
  applyJobChange,
  jobHref,
  listFromSnapshot,
  mergePages,
  overdue,
  queuedAhead,
  waveKey,
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

describe("queue place", () => {
  it("counts the queued jobs of the same kind asked for earlier", () => {
    const rows: ListedJob[] = [
      plate("p3", 3),
      scan("s2", 2),
      plate("p2", 2, { status: "rendering" }),
      plate("p1", 1),
      plate("p0", 0),
    ];
    expect(queuedAhead(rows, rows[0])).toBe(2);
    expect(queuedAhead(rows, rows[1])).toBe(0);
  });

  it("has no place once the job is rendering", () => {
    const job = plate("p1", 1, { status: "rendering" });
    expect(queuedAhead([job], job)).toBeNull();
  });
});

describe("slow jobs", () => {
  const now = Date.parse(at(30));

  it("flags a rendering job that has not reported for longer than the worker renews it", () => {
    const quiet = plate("p", 0, { status: "rendering", updated_at: new Date(now - RENDERING_QUIET_MS - 1).toISOString() });
    const fresh = plate("p", 0, { status: "rendering", updated_at: new Date(now - 1000).toISOString() });
    expect(overdue(quiet, now)).toBe("quiet");
    expect(overdue(fresh, now)).toBeNull();
  });

  it("flags a job that has waited in the queue longer than usual", () => {
    const long = scan("s", 0, { created_at: new Date(now - QUEUED_LONG_MS - 1).toISOString() });
    expect(overdue(long, now)).toBe("waiting");
  });

  it("never flags a finished job", () => {
    expect(overdue(plate("p", 0, { status: "rendered" }), now)).toBeNull();
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
  });
});
