import { describe, expect, it } from "vitest";

import {
  failureSentence,
  runFastqUrl,
  runSraRuns,
  runSteps,
  fetchGenomeLabel,
  isFinished,
  runAttributes,
  runDatasetHref,
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
    genome_version_id: null,
    dataset_id: null,
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

  it("links the dataset's expression page only once the load has linked it", () => {
    expect(runDatasetHref(run())).toBeNull();
    expect(runDatasetHref(run({ dataset_id: 24 }))).toBe("/app/expression/1/24");
    expect(runDatasetHref(run({ dataset_id: 24, metadata: null }))).toBeNull();
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
      de: "waiting",
      "build-h5ad": "waiting",
      "load-dataset": "waiting",
      cleanup: "waiting",
    });
  });

  it("marks every step waiting before the first report", () => {
    expect(Object.values(stepStates(run({ status: "queued", current_step: null })))).toEqual(
      Array(10).fill("waiting")
    );
  });

  const ALL_PODS = Object.fromEntries(
    ["stage-reference", "stage", "qc", "count", "preprocess", "cluster", "de", "build-h5ad", "load-dataset", "cleanup"].map(
      (step, i) => [step, `p${i}`]
    )
  );

  it("marks every step done when the run succeeded", () => {
    expect(
      Object.values(stepStates(run({ status: "succeeded", current_step: "cleanup", step_pods: ALL_PODS })))
    ).toEqual(Array(10).fill("done"));
  });

  it("marks steps a finished run never had as not run", () => {
    const { preprocess: _p, cluster: _c, de: _d, "build-h5ad": _b, ...before } = ALL_PODS;
    expect(stepStates(run({ status: "succeeded", current_step: "cleanup", step_pods: before }))).toMatchObject({
      count: "done",
      preprocess: "not-run",
      cluster: "not-run",
      de: "not-run",
      "build-h5ad": "not-run",
      "load-dataset": "done",
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
      de: "not-run",
      "build-h5ad": "not-run",
      "load-dataset": "not-run",
      cleanup: "not-run",
    });
  });

  it("marks nothing failed when a run failed before starting", () => {
    expect(Object.values(stepStates(run({ status: "failed", current_step: null })))).toEqual(
      Array(10).fill("not-run")
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
      de: "skipped",
      "build-h5ad": "skipped",
      "load-dataset": "skipped",
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

  it("leaves out staging, which an SRA import's reads skip", () => {
    const imported = run({
      params: { sample: "root_tip", reference: "tiny_ref", sra_runs: ["SRR28503597"] },
      current_step: "count",
      step_pods: { "fetch-sra": "wf-fetch-1", qc: "wf-qc-2", count: "wf-count-3" },
    });
    expect(runSteps(imported).map((s) => s.id)).not.toContain("stage");
    expect(stepStates(imported)).not.toHaveProperty("stage");
  });

  it("keeps staging for an earlier SRA import that staged its reads", () => {
    const earlier = run({
      params: { sample: "root_tip", reference: "tiny_ref", sra_runs: ["SRR28503597"] },
      current_step: "count",
      step_pods: { "fetch-sra": "wf-fetch-1", stage: "wf-stage-2" },
    });
    expect(stepStates(earlier)).toMatchObject({ stage: "done", count: "running" });
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
      "de",
      "build-h5ad",
      "load-dataset",
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
    [3, /Cell Ranger reference wasn't found/],
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
    [10, /couldn't be downloaded from ENA or SRA/],
    [11, /don't look like 10x gene-expression reads/],
  ])("explains exit %i from the SRA download", (code, sentence) => {
    const failed = run({ status: "failed", exit_code: code, current_step: "fetch-sra" });
    expect(failureSentence(failed)).toMatch(sentence);
  });

  it("has no sentence for the retired exit 12 from the SRA download", () => {
    const failed = run({
      status: "failed",
      exit_code: 12,
      current_step: "fetch-sra",
      message: "Step fetch-sra failed (exit 12)",
    });
    expect(failureSentence(failed)).toBe("Step fetch-sra failed (exit 12)");
  });

  it.each([
    [6, /final \.h5ad wasn't found/],
    [16, /no Bloom credentials/],
  ])("explains exit %i from loading the dataset", (code, sentence) => {
    const failed = run({ status: "failed", exit_code: code, current_step: "load-dataset" });
    expect(failureSentence(failed)).toMatch(sentence);
  });

  it("gives the service's message for a load bloomctl refused", () => {
    const failed = run({
      status: "failed",
      exit_code: 1,
      current_step: "load-dataset",
      message: "Loading the dataset into Bloom failed; the load-dataset step's log says why",
    });
    expect(failureSentence(failed)).toMatch(/load-dataset step's log says why/);
  });

  it.each([
    [4, /were removed after the run was started/],
    [6, /recorded file list couldn't be used/],
    [7, /in the S3 folder don't follow/],
    [8, /changed after the run was started/],
    [9, /named for another sample/],
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

  it("gives a folder run's count exits 6 and 7 the count sentences, not the folder ones", () => {
    const folder = { sample: "col0", reference: "tiny_ref", fastq_url: "s3://lab-data/run42/" };
    expect(
      failureSentence(run({ status: "failed", exit_code: 6, current_step: "count", params: folder }))
    ).toMatch(/can't be used as a Cell Ranger run id/);
    expect(
      failureSentence(run({ status: "failed", exit_code: 7, current_step: "count", params: folder }))
    ).toMatch(/don't follow Illumina's naming/);
  });

  it("prefers the service's own message for a folder run's stage step", () => {
    const failed = run({
      status: "failed",
      exit_code: 9,
      current_step: "stage",
      message: "The FASTQs in s3://lab-data/run42/ are named for another sample than the run's, col0",
      params: { sample: "col0", reference: "tiny_ref", fastq_url: "s3://lab-data/run42/" },
    });
    expect(failureSentence(failed)).toBe(failed.message);
  });

  it("doesn't borrow a count sentence for an unexpected stage exit of a folder run", () => {
    const failed = run({
      status: "failed",
      exit_code: 3,
      current_step: "stage",
      message: "Step stage failed (exit 3)",
      params: { sample: "col0", reference: "tiny_ref", fastq_url: "s3://lab-data/run42/" },
    });
    expect(failureSentence(failed)).toBe("Step stage failed (exit 3)");
  });

  it("uses the service's message for a folder run's missing or misnamed FASTQs at count", () => {
    const failed = run({
      status: "failed",
      exit_code: 4,
      current_step: "count",
      message: "No FASTQs were found on the shared disk after copying s3://lab-data/run42/",
      params: { sample: "col0", reference: "tiny_ref", fastq_url: "s3://lab-data/run42/" },
    });
    expect(failureSentence(failed)).toBe(failed.message);
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

describe("runFastqUrl", () => {
  it("reads the run's S3 folder, or null", () => {
    const url = "s3://lab-data/run42/";
    expect(runFastqUrl(run({ params: { sample: "s", reference: "r", fastq_url: url } }))).toBe(url);
    expect(runFastqUrl(run())).toBeNull();
    expect(runFastqUrl(run({ params: null }))).toBeNull();
  });
});

describe("a run on a genome version from Bloom", () => {
  const genomeRun = (overrides: Partial<RnaseqRun> = {}) =>
    run({
      params: { sample: "col0", reference: "athaliana_col0.v2" },
      step_pods: { stage: "p1", qc: "p2", "download-genome": "p3", "build-reference": "p4" },
      current_step: "build-reference",
      ...overrides,
    });

  it("goes through the genome steps, not stage-reference", () => {
    const ids = runSteps(genomeRun()).map((s) => s.id);
    expect(ids).toEqual([
      "stage",
      "qc",
      "download-genome",
      "build-reference",
      "count",
      "preprocess",
      "cluster",
      "de",
      "build-h5ad",
      "load-dataset",
      "cleanup",
    ]);
  });

  it("shows the reads' steps done while the reference is still building", () => {
    const states = stepStates(genomeRun());
    expect(states.stage).toBe("done");
    expect(states.qc).toBe("done");
    expect(states["download-genome"]).toBe("done");
    expect(states["build-reference"]).toBe("running");
    expect(states.count).toBe("waiting");
  });

  it("shows a queued run's genome steps too, before any pod", () => {
    const ids = runSteps(genomeRun({ step_pods: {}, current_step: null, status: "queued" })).map(
      (s) => s.id
    );
    expect(ids).toContain("download-genome");
    expect(ids).not.toContain("stage-reference");
  });

  it.each([
    ["download-genome", 3],
    ["download-genome", 6],
    ["build-reference", 17],
    ["build-reference", 3],
  ])("shows the service's message when %s fails with exit %i", (step, code) => {
    const message = "Genome athaliana_col0.v2 isn't available in Bloom";
    const r = genomeRun({ status: "failed", current_step: step, exit_code: code, message });
    expect(failureSentence(r)).toBe(message);
  });
});

describe("a run started before genomes came from Bloom", () => {
  it("keeps its stage-reference step, by its pod or its current step", () => {
    for (const r of [run(), run({ step_pods: {}, current_step: "stage-reference" })]) {
      const ids = runSteps(r).map((s) => s.id);
      expect(ids).toContain("stage-reference");
      expect(ids).not.toContain("download-genome");
      expect(ids).not.toContain("build-reference");
    }
  });
});

describe("fetchGenomeLabel", () => {
  function client(result: { data: unknown; error: unknown }, seen: unknown[] = []) {
    const query = {
      select: (cols: string) => (seen.push(cols), query),
      eq: (col: string, value: unknown) => (seen.push([col, value]), query),
      maybeSingle: async () => result,
    };
    return { from: (table: string) => (seen.push(table), query) };
  }

  it("names the run's version with its assembly and annotation", async () => {
    const seen: unknown[] = [];
    const row = {
      id: 3,
      version: 2,
      assembly: "TAIR10",
      annotation: "Araport11",
      genome_references: { name: "athaliana_col0" },
    };
    const label = await fetchGenomeLabel(
      client({ data: row, error: null }, seen) as never,
      run({ genome_version_id: 3 })
    );
    expect(label).toBe("athaliana_col0 v2 — TAIR10 / Araport11");
    expect(seen).toContainEqual("genome_reference_versions");
    expect(seen).toContainEqual(["id", 3]);
  });

  it("asks nothing for a run without a genome version", async () => {
    const throwing = {
      from: () => {
        throw new Error("should not be called");
      },
    };
    expect(await fetchGenomeLabel(throwing as never, run())).toBeNull();
  });

  it("returns null when the version can't be read", async () => {
    const failing = client({ data: null, error: { message: "denied" } });
    expect(await fetchGenomeLabel(failing as never, run({ genome_version_id: 3 }))).toBeNull();
  });
});
