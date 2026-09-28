/** The live views' reads, asserted per table against the recording mock. */

import { beforeEach, describe, expect, it } from "vitest";
import { mockClient, queriesFor, resetSupabaseMock, supabaseMock, type RecordedQuery } from "./__fixtures__/supabase-mock";
import {
  fetchExperimentMembers,
  fetchExperimentRunIds,
  fetchLatestSources,
  fetchRun,
  fetchRunExperiments,
  fetchRuns,
  fetchRunsByIds,
  fetchRunScans,
  fetchScanMeta,
  isRunInExperiment,
  QueryError,
} from "./queries";

const client = mockClient;
const range = (n: number, from = 1) => Array.from({ length: n }, (_, i) => from + i);

beforeEach(() => resetSupabaseMock());

describe("fetchRuns", () => {
  it("reads the newest 50 runs, created_at then id descending", async () => {
    supabaseMock.respond = () => ({ data: [{ id: 1 }], error: null });
    expect(await fetchRuns(client)).toEqual([{ id: 1 }]);
    const [q] = queriesFor("cyl_pipeline_runs");
    expect(q.all("order")).toEqual([
      ["created_at", { ascending: false }],
      ["id", { ascending: false }],
    ]);
    expect(q.arg("limit")).toEqual([50]);
    expect(q.arg("or")).toBeUndefined();
    expect(q.arg("eq")).toBeUndefined();
    expect(String(q.arg("select")?.[0])).not.toMatch(/reused_count/);
  });

  it("builds the keyset from the raw cursor, with values double-quoted", async () => {
    await fetchRuns(client, { cursor: { created_at: "2026-09-28T10:00:10.1234+00:00", id: 151 } });
    const [q] = queriesFor("cyl_pipeline_runs");
    expect(q.arg("or")).toEqual([
      'created_at.lt."2026-09-28T10:00:10.1234+00:00",and(created_at.eq."2026-09-28T10:00:10.1234+00:00",id.lt.151)',
    ]);
  });

  it("filters by requester on the server for Only mine", async () => {
    await fetchRuns(client, { requestedBy: "4965b3af-ccfe-40f5-814b-447e1f726e1b" });
    expect(queriesFor("cyl_pipeline_runs")[0].arg("eq")).toEqual(["requested_by", "4965b3af-ccfe-40f5-814b-447e1f726e1b"]);
  });
});

describe("fetchRun", () => {
  it("looks one run up with maybeSingle", async () => {
    supabaseMock.respond = () => ({ data: { id: 91 }, error: null });
    expect(await fetchRun(client, 91)).toEqual({ id: 91 });
    const [q] = queriesFor("cyl_pipeline_runs");
    expect(q.arg("eq")).toEqual(["id", 91]);
    expect(q.arg("maybeSingle")).toEqual([]);
  });

  it("answers null for a run the caller can't see", async () => {
    supabaseMock.respond = () => ({ data: null, error: null });
    expect(await fetchRun(client, 91)).toBeNull();
  });
});

describe("fetchRunsByIds", () => {
  it("reads the given runs", async () => {
    await fetchRunsByIds(client, [3, 4]);
    expect(queriesFor("cyl_pipeline_runs")[0].arg("in")).toEqual(["id", [3, 4]]);
  });

  it("makes no request for no ids", async () => {
    expect(await fetchRunsByIds(client, [])).toEqual([]);
    expect(supabaseMock.queries).toHaveLength(0);
  });
});

describe("fetchRunScans", () => {
  it("reads pages of 1000 ordered by scan_id until a page is empty", async () => {
    const rows = range(2500).map((scan_id) => ({ id: scan_id, run_id: 91, scan_id }));
    supabaseMock.respond = (q) => {
      const [from, to] = q.arg("range") as [number, number];
      return { data: rows.slice(from, to + 1), error: null };
    };
    const result = await fetchRunScans(client, 91);
    expect(result).toHaveLength(2500);
    const qs = queriesFor("cyl_pipeline_run_scans");
    expect(qs.map((q) => q.arg("range"))).toEqual([
      [0, 999],
      [1000, 1999],
      [2000, 2999],
      [3000, 3999],
    ]);
    for (const q of qs) {
      expect(q.arg("eq")).toEqual(["run_id", 91]);
      expect(q.arg("order")).toEqual(["scan_id", { ascending: true }]);
    }
  });

  it("makes 6 requests for 5000 rows, the last one empty", async () => {
    const rows = range(5000).map((scan_id) => ({ id: scan_id, run_id: 91, scan_id }));
    supabaseMock.respond = (q) => {
      const [from, to] = q.arg("range") as [number, number];
      return { data: rows.slice(from, to + 1), error: null };
    };
    expect(await fetchRunScans(client, 91)).toHaveLength(5000);
    expect(queriesFor("cyl_pipeline_run_scans")).toHaveLength(6);
  });
});

describe("fetchScanMeta", () => {
  it("reads cyl_scans_extended in chunks of at most 200 ids, with the columns the views need", async () => {
    supabaseMock.respond = (q) => ({
      data: (q.arg("in")![1] as number[]).map((scan_id) => ({ scan_id, qr_code: `Q${scan_id}` })),
      error: null,
    });
    const meta = await fetchScanMeta(client, range(450));
    expect(meta.size).toBe(450);
    expect(meta.get(450)?.qr_code).toBe("Q450");
    const qs = queriesFor("cyl_scans_extended");
    expect(qs.map((q) => (q.arg("in")![1] as number[]).length)).toEqual([200, 200, 50]);
    const columns = String(qs[0].arg("select")![0]);
    for (const c of ["scan_id", "qr_code", "wave_id", "wave_number", "plant_age_days", "species_id", "species_name", "accession_id", "experiment_id"]) {
      expect(columns).toContain(c);
    }
  });

  it("makes no request for no ids", async () => {
    expect((await fetchScanMeta(client, [])).size).toBe(0);
    expect(supabaseMock.queries).toHaveLength(0);
  });
});

describe("fetchLatestSources", () => {
  it("reads scan_id and max_source_id in chunks of at most 200", async () => {
    supabaseMock.respond = (q) => ({
      data: (q.arg("in")![1] as number[]).filter((id) => id !== 3).map((scan_id) => ({ scan_id, max_source_id: scan_id === 2 ? null : scan_id * 10 })),
      error: null,
    });
    const latest = await fetchLatestSources(client, range(201));
    const qs = queriesFor("cyl_scan_latest_source");
    expect(qs.map((q) => (q.arg("in")![1] as number[]).length)).toEqual([200, 1]);
    expect(qs[0].arg("select")).toEqual(["scan_id, max_source_id"]);
    expect(latest.get(1)).toBe(10);
    expect(latest.get(2)).toBeNull();
    expect(latest.has(3)).toBe(false);
  });
});

describe("fetchRunExperiments", () => {
  it("reads the view for the given runs, created_at descending, with each experiment's name and species", async () => {
    supabaseMock.respond = () => ({
      data: [{ run_id: 91, experiment_id: 5, created_at: "t", cyl_experiments: { name: "exp", species_id: 2 } }],
      error: null,
    });
    expect(await fetchRunExperiments(client, [91, 92])).toEqual([
      { run_id: 91, experiment_id: 5, created_at: "t", name: "exp", species_id: 2 },
    ]);
    const [q] = queriesFor("cyl_pipeline_run_experiments");
    expect(q.arg("in")).toEqual(["run_id", [91, 92]]);
    expect(q.arg("order")).toEqual(["created_at", { ascending: false }]);
    expect(String(q.arg("select")![0])).toMatch(/cyl_experiments\(name, species_id\)/);
  });

  it("keeps a row whose experiment embed is missing", async () => {
    supabaseMock.respond = () => ({ data: [{ run_id: 91, experiment_id: 5, created_at: "t", cyl_experiments: null }], error: null });
    expect(await fetchRunExperiments(client, [91])).toEqual([{ run_id: 91, experiment_id: 5, created_at: "t", name: null, species_id: null }]);
  });
});

describe("fetchExperimentRunIds", () => {
  it("reads the 10 most recent runs touching an experiment", async () => {
    supabaseMock.respond = () => ({ data: [{ run_id: 9 }, { run_id: 8 }], error: null });
    expect(await fetchExperimentRunIds(client, 5)).toEqual([9, 8]);
    const [q] = queriesFor("cyl_pipeline_run_experiments");
    expect(q.arg("eq")).toEqual(["experiment_id", 5]);
    expect(q.all("order")).toEqual([
      ["created_at", { ascending: false }],
      ["run_id", { ascending: false }],
    ]);
    expect(q.arg("limit")).toEqual([10]);
  });
});

describe("membership", () => {
  it("answers which runs touch an experiment", async () => {
    supabaseMock.respond = () => ({ data: [{ run_id: 95 }], error: null });
    expect(await fetchExperimentMembers(client, 5, [95, 96])).toEqual(new Set([95]));
    const [q] = queriesFor("cyl_pipeline_run_experiments");
    expect(q.arg("eq")).toEqual(["experiment_id", 5]);
    expect(q.arg("in")).toEqual(["run_id", [95, 96]]);
  });

  it("isRunInExperiment answers for one run", async () => {
    supabaseMock.respond = (q: RecordedQuery) => ({ data: (q.arg("in")![1] as number[]).includes(95) ? [{ run_id: 95 }] : [], error: null });
    expect(await isRunInExperiment(client, 95, 5)).toBe(true);
    expect(await isRunInExperiment(client, 96, 5)).toBe(false);
  });
});

describe("errors", () => {
  const failing = [
    ["fetchRuns", () => fetchRuns(client), "cyl_pipeline_runs"],
    ["fetchRun", () => fetchRun(client, 1), "cyl_pipeline_runs"],
    ["fetchRunsByIds", () => fetchRunsByIds(client, [1]), "cyl_pipeline_runs"],
    ["fetchRunScans", () => fetchRunScans(client, 1), "cyl_pipeline_run_scans"],
    ["fetchScanMeta", () => fetchScanMeta(client, [1]), "cyl_scans_extended"],
    ["fetchLatestSources", () => fetchLatestSources(client, [1]), "cyl_scan_latest_source"],
    ["fetchRunExperiments", () => fetchRunExperiments(client, [1]), "cyl_pipeline_run_experiments"],
    ["fetchExperimentRunIds", () => fetchExperimentRunIds(client, 1), "cyl_pipeline_run_experiments"],
    ["fetchExperimentMembers", () => fetchExperimentMembers(client, 1, [1]), "cyl_pipeline_run_experiments"],
  ] as const;

  it.each(failing)("%s surfaces a typed error naming its relation", async (_name, call, relation) => {
    supabaseMock.respond = () => ({ data: null, error: { message: "relation does not exist", code: "42P01" } });
    const error = await call().catch((e: unknown) => e);
    expect(error).toBeInstanceOf(QueryError);
    expect(error).toMatchObject({ relation, code: "42P01", message: "relation does not exist" });
  });
});
