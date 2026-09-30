import { describe, expect, it } from "vitest";

import {
  failureSentence,
  fetchRequesters,
  isFinished,
  runAttributes,
  runDatasetName,
  runReference,
  runSample,
  runSpeciesId,
  stepStarted,
  stepStates,
  upsertRun,
  type RnaseqRun,
} from "./rnaseq-runs";

function run(overrides: Partial<RnaseqRun> = {}): RnaseqRun {
  return {
    id: 1,
    workflow_type: "scrna-cellranger",
    status: "running",
    current_step: "qc",
    params: { sample: "tinygex", reference: "tiny_ref" },
    metadata: {
      species_id: 1,
      dataset_name: "TEST tinygex",
      origin: "hpi",
      attributes: { tissue: "root", replicate: 2 },
    },
    requested_by: "user-1",
    argo_workflow_name: "scrna-cellranger-staging-1-abc",
    step_pods: { "stage-reference": "p1", stage: "p2", qc: "p3" },
    exit_code: null,
    message: null,
    created_at: "2026-09-30T00:03:00Z",
    submitted_at: "2026-09-30T00:03:54Z",
    completed_at: null,
    ...overrides,
  };
}

describe("reading a run", () => {
  it("reads the inputs and the dataset details", () => {
    const r = run();
    expect(runSample(r)).toBe("tinygex");
    expect(runReference(r)).toBe("tiny_ref");
    expect(runDatasetName(r)).toBe("TEST tinygex");
    expect(runSpeciesId(r)).toBe(1);
    expect(runAttributes(r)).toEqual([
      ["tissue", "root"],
      ["replicate", "2"],
    ]);
  });

  it("copes with a run started without metadata", () => {
    const r = run({ metadata: null, params: {} });
    expect(runSample(r)).toBeNull();
    expect(runDatasetName(r)).toBeNull();
    expect(runSpeciesId(r)).toBeNull();
    expect(runAttributes(r)).toEqual([]);
  });

  it("knows which steps have a log", () => {
    expect(stepStarted(run(), "qc")).toBe(true);
    expect(stepStarted(run(), "count")).toBe(false);
    expect(stepStarted(run({ step_pods: null }), "qc")).toBe(false);
  });

  it.each([
    ["queued", false],
    ["submitted", false],
    ["running", false],
    ["succeeded", true],
    ["skipped", true],
    ["failed", true],
  ])("treats %s as finished: %s", (status, finished) => {
    expect(isFinished(status)).toBe(finished);
  });
});

describe("stepStates", () => {
  it("marks steps before the current one done, it running and the rest waiting", () => {
    expect(stepStates(run())).toEqual({
      "stage-reference": "done",
      stage: "done",
      qc: "running",
      count: "waiting",
      cleanup: "waiting",
    });
  });

  it("marks every step waiting before the first report", () => {
    expect(Object.values(stepStates(run({ status: "queued", current_step: null })))).toEqual(
      Array(5).fill("waiting")
    );
  });

  it("marks every step done when the run succeeded", () => {
    expect(Object.values(stepStates(run({ status: "succeeded", current_step: "cleanup" })))).toEqual(
      Array(5).fill("done")
    );
  });

  it("marks the step a run failed at, and the ones after it as not run", () => {
    expect(stepStates(run({ status: "failed", current_step: "count" }))).toEqual({
      "stage-reference": "done",
      stage: "done",
      qc: "done",
      count: "failed",
      cleanup: "not-run",
    });
  });

  it("marks nothing failed when a run failed before starting", () => {
    expect(Object.values(stepStates(run({ status: "failed", current_step: null })))).toEqual(
      Array(5).fill("not-run")
    );
  });

  it("marks the steps a skipped run never started as skipped", () => {
    const states = stepStates(
      run({ status: "skipped", current_step: "stage", step_pods: { "stage-reference": "p1", stage: "p2" } })
    );
    expect(states).toEqual({
      "stage-reference": "done",
      stage: "done",
      qc: "skipped",
      count: "skipped",
      cleanup: "skipped",
    });
  });
});

describe("failureSentence", () => {
  it.each([
    [3, /reference\.json/],
    [4, /No FASTQ files/],
    [5, /Cell Ranger count failed/],
    [6, /run id/],
  ])("explains exit %i", (code, sentence) => {
    expect(failureSentence(run({ status: "failed", exit_code: code }))).toMatch(sentence);
  });

  it("falls back to the run's message for another exit code", () => {
    expect(failureSentence(run({ status: "failed", exit_code: 137, message: "OOMKilled" }))).toBe(
      "OOMKilled"
    );
  });

  it("says nothing for a run that didn't fail", () => {
    expect(failureSentence(run({ status: "succeeded", exit_code: 0 }))).toBeNull();
  });
});

describe("upsertRun", () => {
  it("adds a new run in id order and replaces an existing one", () => {
    const list = [run({ id: 3 }), run({ id: 1 })];
    expect(upsertRun(list, run({ id: 2 })).map((r) => r.id)).toEqual([3, 2, 1]);
    const replaced = upsertRun(list, run({ id: 1, status: "succeeded" }));
    expect(replaced.map((r) => [r.id, r.status])).toEqual([
      [3, "running"],
      [1, "succeeded"],
    ]);
  });

  it("drops a run the filter rejects", () => {
    const list = [run({ id: 2 }), run({ id: 1 })];
    expect(upsertRun(list, run({ id: 1 }), () => false).map((r) => r.id)).toEqual([2]);
  });
});

describe("fetchRequesters", () => {
  it("maps run ids to emails", async () => {
    const client = {
      rpc: async (fn: string, args: unknown) => {
        expect(fn).toBe("rnaseq_run_requesters");
        expect(args).toEqual({ p_run_ids: [1, 2] });
        return { data: [{ run_id: 1, email: "a@salk.edu" }], error: null };
      },
    };
    expect(await fetchRequesters(client as never, [1, 2])).toEqual(new Map([[1, "a@salk.edu"]]));
  });

  it("asks nothing for no runs, and returns nothing on an error", async () => {
    const client = {
      rpc: async () => {
        throw new Error("should not be called");
      },
    };
    expect(await fetchRequesters(client as never, [])).toEqual(new Map());
    const failing = { rpc: async () => ({ data: null, error: { message: "denied" } }) };
    expect(await fetchRequesters(failing as never, [1])).toEqual(new Map());
  });
});
