/** What the Video Jobs page asks the database for. */

import { beforeEach, describe, expect, it } from "vitest";
import { mockClient, queriesFor, resetSupabaseMock } from "@/lib/cyl-pipeline/__fixtures__/supabase-mock";
import { fetchActiveJobs, fetchJobById, fetchJobPlaces, fetchJobs } from "./queries";
import type { ListedJob } from "./list";

beforeEach(() => resetSupabaseMock());

describe("fetchJobs", () => {
  it("reads one page of each job table, newest first", async () => {
    await fetchJobs(mockClient);
    for (const table of ["gravi_plate_video_jobs", "cyl_video_jobs"]) {
      const [q] = queriesFor(table);
      expect(q.arg("order")).toEqual(["created_at", { ascending: false }]);
      expect(q.arg("limit")).toEqual([50]);
    }
  });

  it("reads only the table of the kind asked for", async () => {
    await fetchJobs(mockClient, { kind: "scan" });
    expect(queriesFor("gravi_plate_video_jobs")).toHaveLength(0);
    expect(queriesFor("cyl_video_jobs")).toHaveLength(1);
  });

  it("passes each filter to the query", async () => {
    await fetchJobs(mockClient, { requestedBy: "me", activeOnly: true, before: "2026-10-29T10:00:00Z" });
    const [q] = queriesFor("cyl_video_jobs");
    expect(q.all("eq")).toContainEqual(["requested_by", "me"]);
    expect(q.arg("in")).toEqual(["status", ["queued", "rendering"]]);
    expect(q.arg("lte")).toEqual(["created_at", "2026-10-29T10:00:00Z"]);
  });

  it("says which table failed", async () => {
    resetSupabaseMock((q) =>
      q.table === "cyl_video_jobs" ? { data: null, error: { message: "denied" } } : { data: [], error: null }
    );
    await expect(fetchJobs(mockClient)).rejects.toThrow("cyl_video_jobs: denied");
  });
});

describe("fetchJobPlaces", () => {
  it("looks up each experiment and scan once, and nothing when there are no jobs", async () => {
    const jobs = [
      { kind: "plate", experiment_id: 12 },
      { kind: "plate", experiment_id: 12 },
      { kind: "scan", scan_id: 44, experiment_id: 3 },
    ] as ListedJob[];
    await fetchJobPlaces(mockClient, jobs);
    expect(queriesFor("gravi_experiments")[0].arg("in")).toEqual(["id", [12]]);
    expect(queriesFor("cyl_scans_extended")[0].arg("in")).toEqual(["scan_id", [44]]);
    expect(queriesFor("cyl_experiments")[0].arg("in")).toEqual(["id", [3]]);

    resetSupabaseMock();
    await fetchJobPlaces(mockClient, []);
    expect(queriesFor("gravi_experiments")).toHaveLength(0);
  });
});

describe("cylinder places", () => {
  it("takes names and species from the experiments table, not the scan view", async () => {
    resetSupabaseMock((q) =>
      q.table === "cyl_scans_extended"
        ? { data: [{ scan_id: 44, wave_id: 7, accession_id: 9, plant_age_days: 5 }], error: null }
        : q.table === "cyl_experiments"
          ? { data: [{ id: 3, name: "Cyl-3", species_id: 1 }], error: null }
          : { data: [], error: null }
    );
    const { scans } = await fetchJobPlaces(mockClient, [{ kind: "scan", scan_id: 44, experiment_id: 3 } as ListedJob]);
    expect(scans).toEqual([
      { scan_id: 44, wave_id: 7, accession_id: 9, plant_age_days: 5, experiment_name: "Cyl-3", species_id: 1 },
    ]);
    expect(String(queriesFor("cyl_scans_extended")[0].arg("select")?.[0])).not.toMatch(/experiment_name|species/);
  });

  it("leaves a scan whose experiment the caller cannot read without a name or link", async () => {
    resetSupabaseMock((q) =>
      q.table === "cyl_scans_extended"
        ? { data: [{ scan_id: 44, wave_id: 7, accession_id: 9, plant_age_days: 5 }], error: null }
        : { data: [], error: null }
    );
    const { scans } = await fetchJobPlaces(mockClient, [{ kind: "scan", scan_id: 44, experiment_id: 3 } as ListedJob]);
    expect(scans[0]).toMatchObject({ experiment_name: null, species_id: null });
  });
});

describe("fetchActiveJobs", () => {
  it("reads every queued or rendering job of both kinds, whoever asked", async () => {
    resetSupabaseMock((q) => ({ data: [{ id: q.table, status: "queued", created_at: "t", updated_at: "t" }], error: null }));
    const active = await fetchActiveJobs(mockClient);
    expect(active.map((a) => a.kind)).toEqual(["plate", "scan"]);
    for (const table of ["gravi_plate_video_jobs", "cyl_video_jobs"]) {
      const [q] = queriesFor(table);
      expect(q.arg("in")).toEqual(["status", ["queued", "rendering"]]);
      expect(q.all("eq")).toEqual([]);
    }
  });
});

describe("fetchJobById", () => {
  it("finds a job in whichever table holds it", async () => {
    resetSupabaseMock((q) =>
      q.table === "cyl_video_jobs" ? { data: [{ id: "j1", scan_id: 44 }], error: null } : { data: [], error: null }
    );
    expect(await fetchJobById(mockClient, "j1")).toMatchObject({ kind: "scan", id: "j1" });
  });

  it("answers null when neither does", async () => {
    expect(await fetchJobById(mockClient, "nope")).toBeNull();
  });
});
