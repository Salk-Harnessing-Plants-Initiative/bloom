import { describe, expect, it } from "vitest";

import {
  failureSentence,
  runFastqUrl,
  runSraRuns,
  runSteps,
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
      preprocess: "waiting",
      cluster: "waiting",
      "build-h5ad": "waiting",
      cleanup: "waiting",
    });
  });

  it("marks every step waiting before the first report", () => {
    expect(Object.values(stepStates(run({ status: "queued", current_step: null })))).toEqual(
      Array(8).fill("waiting")
    );
  });

  const ALL_PODS = Object.fromEntries(
    ["stage-reference", "stage", "qc", "count", "preprocess", "cluster", "build-h5ad", "cleanup"].map(
      (step, i) => [step, `p${i}`]
    )
  );

  it("marks every step done when the run succeeded", () => {
    expect(
      Object.values(stepStates(run({ status: "succeeded", current_step: "cleanup", step_pods: ALL_PODS })))
    ).toEqual(Array(8).fill("done"));
  });

  it("marks steps a finished run never had as not run", () => {
    const { preprocess: _p, cluster: _c, "build-h5ad": _b, ...before } = ALL_PODS;
    expect(stepStates(run({ status: "succeeded", current_step: "cleanup", step_pods: before }))).toMatchObject({
      count: "done",
      preprocess: "not-run",
      cluster: "not-run",
      "build-h5ad": "not-run",
      cleanup: "done",
    });
  });

  it("marks the step a run failed at, and the ones after it as not run", () => {
    expect(stepStates(run({ status: "failed", current_step: "count" }))).toEqual({
      "stage-reference": "done",
      stage: "done",
      qc: "done",
      count: "failed",
      preprocess: "not-run",
      cluster: "not-run",
      "build-h5ad": "not-run",
      cleanup: "not-run",
    });
  });

  it("marks nothing failed when a run failed before starting", () => {
    expect(Object.values(stepStates(run({ status: "failed", current_step: null })))).toEqual(
      Array(8).fill("not-run")
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
      preprocess: "skipped",
      cluster: "skipped",
      "build-h5ad": "skipped",
      cleanup: "skipped",
    });
  });

  it("starts an SRA import with the download, which runs before staging", () => {
    const imported = run({
      params: { sample: "root_tip", reference: "tiny_ref", sra_runs: ["SRR28503597"] },
      current_step: "fetch-sra",
    });
    expect(runSteps(imported)[0].id).toBe("fetch-sra");
    expect(stepStates(imported)).toMatchObject({
      "fetch-sra": "running",
      "stage-reference": "waiting",
      "build-h5ad": "waiting",
    });
  });

  it("marks the download done once the run has moved on", () => {
    const imported = run({
      params: { sample: "root_tip", reference: "tiny_ref", sra_runs: ["SRR28503597"] },
      current_step: "cluster",
    });
    expect(stepStates(imported)).toMatchObject({
      "fetch-sra": "done",
      count: "done",
      preprocess: "done",
      cluster: "running",
      "build-h5ad": "waiting",
    });
  });
});

describe("runSteps and runSraRuns", () => {
  it("leaves the download out of a run without SRA run IDs", () => {
    expect(runSteps(run()).map((s) => s.id)).toEqual([
      "stage-reference",
      "stage",
      "qc",
      "count",
      "preprocess",
      "cluster",
      "build-h5ad",
      "cleanup",
    ]);
    expect(runSraRuns(run())).toEqual([]);
  });

  it("reads the run IDs in lane order", () => {
    const ids = ["SRR28503598", "SRR28503597"];
    expect(runSraRuns(run({ params: { sample: "s", reference: "r", sra_runs: ids } }))).toEqual(ids);
  });
});

describe("failureSentence", () => {
  it.each([
    [3, /reference\.json/],
    [4, /No FASTQ files/],
    [5, /Cell Ranger count failed/],
    [6, /run id/],
    [7, /Illumina's naming/],
    [13, /Fewer than 50 cells/],
    [14, /count matrix wasn't found/],
    [15, /didn't fit/],
  ])("explains exit %i", (code, sentence) => {
    expect(failureSentence(run({ status: "failed", exit_code: code }))).toMatch(sentence);
  });

  it.each([
    [6, /SRA run IDs can't be used/],
    [7, /couldn't be named the Illumina way/],
    [10, /couldn't be downloaded from SRA/],
    [11, /don't look like 10x gene-expression reads/],
    [12, /already holds other FASTQs/],
  ])("explains exit %i from the SRA download", (code, sentence) => {
    const failed = run({ status: "failed", exit_code: code, current_step: "fetch-sra" });
    expect(failureSentence(failed)).toMatch(sentence);
  });

  it.each([
    [4, /even after waiting 10 minutes/],
    [7, /in the S3 folder don't follow/],
    [8, /changed after the run was started/],
    [10, /couldn't be listed or copied/],
  ])("explains exit %i from staging an S3 folder", (code, sentence) => {
    const failed = run({
      status: "failed",
      exit_code: code,
      current_step: "stage",
      params: { sample: "col0", reference: "tiny_ref", fastq_url: "s3://lab-data/run42/" },
    });
    expect(failureSentence(failed)).toMatch(sentence);
  });

  it("keeps the folder sentences to the stage step of a folder run", () => {
    expect(failureSentence(run({ status: "failed", exit_code: 4, current_step: "stage" }))).toMatch(
      /in the sample's folder/
    );
    const folderCount = run({
      status: "failed",
      exit_code: 5,
      current_step: "count",
      params: { sample: "col0", reference: "tiny_ref", fastq_url: "s3://lab-data/run42/" },
    });
    expect(failureSentence(folderCount)).toMatch(/Cell Ranger count failed/);
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

describe("runFastqUrl", () => {
  it("reads the run's S3 folder, or null", () => {
    const url = "s3://lab-data/run42/";
    expect(runFastqUrl(run({ params: { sample: "s", reference: "r", fastq_url: url } }))).toBe(url);
    expect(runFastqUrl(run())).toBeNull();
    expect(runFastqUrl(run({ params: null }))).toBeNull();
  });
});
