/** What the Video Jobs page asks the database for. */

import { beforeEach, describe, expect, it } from "vitest";
import { mockClient, queriesFor, resetSupabaseMock } from "@/lib/cyl-pipeline/__fixtures__/supabase-mock";
import { fetchJobPlaces, fetchJobs } from "./queries";
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

    resetSupabaseMock();
    await fetchJobPlaces(mockClient, []);
    expect(queriesFor("gravi_experiments")).toHaveLength(0);
  });
});
