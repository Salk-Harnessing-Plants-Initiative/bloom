/** The live views' reads, asserted per table against the recording mock. */

import { beforeEach, describe, expect, it } from "vitest";
import { mockClient, queriesFor, resetSupabaseMock, supabaseMock, type RecordedQuery } from "./__fixtures__/supabase-mock";
import { runRow } from "./__fixtures__/rows";
import {
  fetchConcurrentRuns,
  fetchExperimentMembers,
  fetchExperimentRunIds,
  fetchLatestSources,
  fetchRun,
  fetchRunExperiments,
  fetchRuns,
  fetchRunsByIds,
  fetchRunScans,
  fetchScanMeta,
  fetchScansWithImages,
  fetchTargetScans,
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
    // cyl_scans_extended runs with its owner's rights, so its names include soft-deleted experiments.
    expect(columns).not.toMatch(/experiment_name/);
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
    ["fetchTargetScans", () => fetchTargetScans(client, { target_level: "wave", target_id: 1 }), "cyl_scans_extended"],
    ["fetchConcurrentRuns", () => fetchConcurrentRuns(client, [1]), "cyl_pipeline_run_experiments"],
    ["fetchScansWithImages", () => fetchScansWithImages(client, [1]), "cyl_scans"],
  ] as const;

  it.each(failing)("%s surfaces a typed error naming its relation", async (_name, call, relation) => {
    supabaseMock.respond = () => ({ data: null, error: { message: "relation does not exist", code: "42P01" } });
    const error = await call().catch((e: unknown) => e);
    expect(error).toBeInstanceOf(QueryError);
    expect(error).toMatchObject({ relation, code: "42P01", message: "relation does not exist" });
  });
});

describe("fetchTargetScans", () => {
  /** Answer cyl_scans_extended from `rows`, honouring eq, in, order and range as PostgREST would. */
  function serve(rows: { scan_id: number; wave_id?: number; experiment_id?: number }[]) {
    supabaseMock.respond = (q) => {
      let data = [...rows];
      for (const [column, value] of q.all("eq")) data = data.filter((r) => (r as Record<string, unknown>)[column as string] === value);
      for (const [column, values] of q.all("in"))
        data = data.filter((r) => (values as unknown[]).includes((r as Record<string, unknown>)[column as string]));
      data.sort((a, b) => a.scan_id - b.scan_id);
      const window = q.arg("range") as [number, number] | undefined;
      return { data: window ? data.slice(window[0], window[1] + 1) : data, error: null };
    };
  }

  it.each([
    ["scan", "scan_id"],
    ["wave", "wave_id"],
    ["experiment", "experiment_id"],
  ] as const)("enumerates a %s target with the trigger's filter on %s", async (target_level, column) => {
    serve([{ scan_id: 7, wave_id: 7, experiment_id: 7 }]);
    const scans = await fetchTargetScans(client, { target_level, target_id: 7 });
    expect(scans.map((s) => s.scan_id)).toEqual([7]);
    const qs = queriesFor("cyl_scans_extended");
    expect(qs.length).toBeGreaterThan(0);
    for (const q of qs) {
      expect(q.all("eq")).toEqual([[column, 7]]);
      expect(q.arg("in")).toBeUndefined();
    }
  });

  it("reads an experiment in pages of 1000 ordered by scan_id until a page is empty: 2,500 scans give N = 2500", async () => {
    serve(range(2500).map((scan_id) => ({ scan_id, experiment_id: 5 })));
    const scans = await fetchTargetScans(client, { target_level: "experiment", target_id: 5 });
    expect(scans).toHaveLength(2500);
    expect(new Set(scans.map((s) => s.scan_id)).size).toBe(2500);
    const qs = queriesFor("cyl_scans_extended");
    expect(qs.map((q) => q.arg("range"))).toEqual([
      [0, 999],
      [1000, 1999],
      [2000, 2999],
      [3000, 3999],
    ]);
    for (const q of qs) expect(q.arg("order")).toEqual(["scan_id", { ascending: true }]);
  });

  it("reads the columns the dialog needs, and not the owner-rights experiment name", async () => {
    await fetchTargetScans(client, { target_level: "wave", target_id: 1 });
    const columns = String(queriesFor("cyl_scans_extended")[0].arg("select")![0]);
    for (const c of ["scan_id", "species_name", "plant_age_days", "experiment_id"]) expect(columns).toContain(c);
    expect(columns).not.toMatch(/experiment_name/);
  });

  it("sends a scan_ids selection in chunks of at most 200 ids, ordered by scan_id", async () => {
    serve(range(450).map((scan_id) => ({ scan_id })));
    const scans = await fetchTargetScans(client, { target_level: "scan_ids", scan_ids: range(450) });
    expect(scans).toHaveLength(450);
    const qs = queriesFor("cyl_scans_extended");
    const chunks = qs.map((q) => q.arg("in")![1] as number[]);
    expect(chunks.map((c) => c.length)).toEqual([200, 200, 50]);
    expect(chunks.flat()).toEqual(range(450));
    for (const q of qs) {
      expect(q.arg("in")![0]).toBe("scan_id");
      expect(q.arg("order")).toEqual(["scan_id", { ascending: true }]);
    }
  });

  it("answers only the selected scans that exist", async () => {
    serve([{ scan_id: 1 }, { scan_id: 3 }]);
    const scans = await fetchTargetScans(client, { target_level: "scan_ids", scan_ids: [1, 2, 3] });
    expect(scans.map((s) => s.scan_id)).toEqual([1, 3]);
  });

  it("answers nothing for a target with no scans", async () => {
    serve([]);
    expect(await fetchTargetScans(client, { target_level: "wave", target_id: 9 })).toEqual([]);
  });
});

describe("fetchConcurrentRuns", () => {
  const NOW = Date.parse("2026-09-29T12:00:00Z");

  /** The view answers `touching` for the asked experiments; runs answer `runs`, honouring in, not and limit. */
  function serve(touching: { run_id: number; experiment_id: number }[], runs: ReturnType<typeof runRow>[]) {
    supabaseMock.respond = (q) => {
      if (q.table === "cyl_pipeline_run_experiments") {
        const exps = q.all("in").find(([c]) => c === "experiment_id")![1] as number[];
        return { data: touching.filter((t) => exps.includes(t.experiment_id)), error: null };
      }
      const ids = q.all("in").find(([c]) => c === "id")![1] as number[];
      const rows = runs.filter((r) => ids.includes(r.id) && !(q.arg("not") && ["complete", "failed"].includes(r.status)));
      return { data: rows, error: null };
    };
  }

  it("asks the view which runs touched the experiments within 7 days, then reads those that are not complete or failed", async () => {
    serve([{ run_id: 88, experiment_id: 5 }], [runRow(88, "2026-09-29T11:48:00+00:00", { status: "running" })]);
    await fetchConcurrentRuns(client, [5, 6], NOW);
    const [view] = queriesFor("cyl_pipeline_run_experiments");
    expect(view.all("in")).toEqual([["experiment_id", [5, 6]]]);
    expect(view.arg("gte")).toEqual(["created_at", "2026-09-22T12:00:00.000Z"]);
    const [q] = queriesFor("cyl_pipeline_runs");
    expect(q.arg("in")).toEqual(["id", [88]]);
    expect(q.arg("not")).toEqual(["status", "in", "(complete,failed)"]);
    expect(q.arg("limit")).toBeUndefined();
    expect(String(q.arg("select")![0])).not.toMatch(/reused_count/);
  });

  it("keeps runs whose counts are incomplete, newest first, de-duplicated across experiments", async () => {
    const runs = [
      runRow(85, "2026-09-29T11:20:00+00:00", { status: "submitted", scan_count: 3 }),
      runRow(88, "2026-09-29T11:48:00+00:00", { status: "running", scan_count: 40, done_count: 10 }),
      // Counts settled: finished whatever its status says, so not concurrent.
      runRow(87, "2026-09-29T11:40:00+00:00", { status: "partial", scan_count: 50, done_count: 25, failed_count: 25 }),
      runRow(86, "2026-09-29T11:30:00+00:00", { status: "complete", scan_count: 12 }),
    ];
    serve(
      [
        { run_id: 88, experiment_id: 5 },
        { run_id: 88, experiment_id: 6 },
        { run_id: 87, experiment_id: 5 },
        { run_id: 86, experiment_id: 5 },
        { run_id: 85, experiment_id: 6 },
      ],
      runs,
    );
    const result = await fetchConcurrentRuns(client, [5, 6], NOW);
    expect(result).toEqual({ runs: [runs[1], runs[0]], more: 0 });
    expect(queriesFor("cyl_pipeline_runs")[0].arg("in")![1]).toEqual([88, 87, 86, 85]);
  });

  it("returns at most 10, and the true count of the rest", async () => {
    const runs = range(25).map((i) => runRow(100 - i, `2026-09-29T11:${String(40 - i).padStart(2, "0")}:00+00:00`, { status: "running" }));
    serve(
      runs.map((r) => ({ run_id: r.id, experiment_id: 5 })),
      runs,
    );
    const result = await fetchConcurrentRuns(client, [5], NOW);
    expect(result.runs.map((r) => r.id)).toEqual(range(10).map((i) => 100 - i));
    expect(result.more).toBe(15);
  });

  it("reads no runs when the view names none, and asks nothing for no experiments", async () => {
    serve([], []);
    expect(await fetchConcurrentRuns(client, [5], NOW)).toEqual({ runs: [], more: 0 });
    expect(queriesFor("cyl_pipeline_runs")).toHaveLength(0);
    expect(await fetchConcurrentRuns(client, [], NOW)).toEqual({ runs: [], more: 0 });
    expect(queriesFor("cyl_pipeline_run_experiments")).toHaveLength(1);
  });

  it("surfaces a view failure as a typed error", async () => {
    supabaseMock.respond = () => ({ data: null, error: { message: "boom", code: "XX000" } });
    await expect(fetchConcurrentRuns(client, [5], NOW)).rejects.toMatchObject({ relation: "cyl_pipeline_run_experiments" });
  });
});

describe("fetchScansWithImages", () => {
  it("asks for at most one image per scan, in chunks of at most 200, and answers the scans that have one", async () => {
    supabaseMock.respond = (q) => ({
      data: (q.arg("in")![1] as number[]).map((id) => ({ id, cyl_images: id % 2 ? [{ id: id * 10 }] : [] })),
      error: null,
    });
    const withImages = await fetchScansWithImages(client, range(450));
    expect(withImages.size).toBe(225);
    expect(withImages.has(1)).toBe(true);
    expect(withImages.has(2)).toBe(false);
    const qs = queriesFor("cyl_scans");
    expect(qs.map((q) => (q.arg("in")![1] as number[]).length)).toEqual([200, 200, 50]);
    for (const q of qs) {
      expect(q.arg("select")).toEqual(["id, cyl_images(id)"]);
      expect(q.arg("in")![0]).toBe("id");
      expect(q.arg("limit")).toEqual([1, { referencedTable: "cyl_images" }]);
    }
  });

  it("makes no request for no ids", async () => {
    expect((await fetchScansWithImages(client, [])).size).toBe(0);
    expect(supabaseMock.queries).toHaveLength(0);
  });
});
