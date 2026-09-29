import { describe, expect, it } from "vitest";

import {
  buildRunMetadata,
  datasetDetailsProblem,
  formatBytes,
  isStartedRun,
  startRunErrorMessage,
  type DatasetDetails,
} from "./scrna-jobs";

const DETAILS: DatasetDetails = {
  speciesId: 1,
  datasetName: "Col-0 root tip",
  accession: "",
  experimentName: "",
  origin: "hpi",
  sourceUrl: "",
  citation: "",
  attributes: [{ key: "", value: "" }],
};

const PUBLIC: DatasetDetails = {
  ...DETAILS,
  origin: "public",
  sourceUrl: " https://doi.org/10.1016/j.devcel.2022.01.008 ",
  citation: " Shahan et al. 2022 ",
};

describe("datasetDetailsProblem", () => {
  it("accepts the required fields alone", () => {
    expect(datasetDetailsProblem(DETAILS)).toBeNull();
  });

  it.each([
    [{ speciesId: null }, "Choose a species."],
    [{ datasetName: "  " }, "Enter a dataset name."],
    [{ attributes: [{ key: " ", value: "root" }] }, "Every extra field needs a name."],
    [
      { attributes: [{ key: "tissue", value: "a" }, { key: " tissue ", value: "b" }] },
      'The field "tissue" is listed twice.',
    ],
  ])("refuses %j", (change, problem) => {
    expect(datasetDetailsProblem({ ...DETAILS, ...change })).toBe(problem);
  });

  it("accepts a public dataset with a web link", () => {
    expect(datasetDetailsProblem(PUBLIC)).toBeNull();
    expect(datasetDetailsProblem({ ...PUBLIC, citation: "" })).toBeNull();
  });

  it.each([
    ["", "Enter the public dataset's source link."],
    ["   ", "Enter the public dataset's source link."],
    ["doi.org/10.1016/x", "The source link must be a web address starting with https://."],
    ["ftp://ftp.ncbi.nlm.nih.gov/geo", "The source link must be a web address starting with https://."],
    ["javascript:alert(1)", "The source link must be a web address starting with https://."],
    [`https://example.org/${"x".repeat(2000)}`, "The source link must be a web address starting with https://."],
  ])("refuses the public source link %j", (sourceUrl, problem) => {
    expect(datasetDetailsProblem({ ...PUBLIC, sourceUrl })).toBe(problem);
  });

  it("refuses a citation over 500 characters", () => {
    expect(datasetDetailsProblem({ ...PUBLIC, citation: "x".repeat(501) })).toBe(
      "The citation is at most 500 characters."
    );
  });

  it("ignores the source fields for HPI data", () => {
    expect(
      datasetDetailsProblem({ ...DETAILS, sourceUrl: "not a link", citation: "x".repeat(501) })
    ).toBeNull();
  });

  it("ignores blank extra rows", () => {
    expect(
      datasetDetailsProblem({
        ...DETAILS,
        attributes: [{ key: " ", value: " " }, { key: "", value: "" }],
      })
    ).toBeNull();
  });
});

describe("buildRunMetadata", () => {
  it("keeps only the required fields when nothing else is given", () => {
    expect(buildRunMetadata(DETAILS)).toEqual({
      species_id: 1,
      dataset_name: "Col-0 root tip",
      origin: "hpi",
    });
  });

  it("adds a public dataset's source link and citation, trimmed", () => {
    expect(buildRunMetadata(PUBLIC)).toEqual({
      species_id: 1,
      dataset_name: "Col-0 root tip",
      origin: "public",
      source_url: "https://doi.org/10.1016/j.devcel.2022.01.008",
      citation: "Shahan et al. 2022",
    });
    expect(buildRunMetadata({ ...PUBLIC, citation: " " })).not.toHaveProperty("citation");
  });

  it("leaves the source fields out for HPI data, even if typed before switching", () => {
    const metadata = buildRunMetadata({ ...DETAILS, sourceUrl: "https://x.org", citation: "X" });
    expect(metadata).not.toHaveProperty("source_url");
    expect(metadata).not.toHaveProperty("citation");
  });

  it("trims every value and keeps named rows, even with an empty value", () => {
    expect(
      buildRunMetadata({
        speciesId: 4,
        datasetName: " Rice leaf ",
        accession: " Nipponbare ",
        experimentName: " Drought 2026 ",
        origin: "hpi",
        sourceUrl: "",
        citation: "",
        attributes: [
          { key: " tissue ", value: " leaf " },
          { key: "notes", value: "" },
          { key: "", value: "" },
        ],
      })
    ).toEqual({
      species_id: 4,
      dataset_name: "Rice leaf",
      accession: "Nipponbare",
      experiment_name: "Drought 2026",
      origin: "hpi",
      attributes: { tissue: "leaf", notes: "" },
    });
  });

  it("throws without a species", () => {
    expect(() => buildRunMetadata({ ...DETAILS, speciesId: null })).toThrow();
  });
});

describe("isStartedRun", () => {
  it("accepts the service's answer", () => {
    expect(
      isStartedRun({ run_id: 1, sample: "s", reference: "r", run_key: "k" })
    ).toBe(true);
  });

  it.each([
    [null],
    ["ok"],
    [{ run_id: "1", sample: "s", reference: "r", run_key: "k" }],
    [{ run_id: 1, sample: "s", reference: "r" }],
  ])("refuses %j", (value) => {
    expect(isStartedRun(value)).toBe(false);
  });
});

describe("startRunErrorMessage", () => {
  it("prefers the service's detail", () => {
    expect(startRunErrorMessage(422, "Sample names can't contain '__'")).toBe(
      "Sample names can't contain '__'"
    );
  });

  it.each([
    [401, "Sign in to start a job."],
    [422, "The sample or reference name isn't valid."],
    [429, "Too many requests. Wait a minute and try again."],
    [503, "The job service isn't available right now."],
    [500, "Couldn't start the job. Try again shortly."],
  ])("has a fixed message for %i", (status, message) => {
    expect(startRunErrorMessage(status, null)).toBe(message);
    expect(startRunErrorMessage(status, "  ")).toBe(message);
  });
});

describe("formatBytes", () => {
  it.each([
    [null, null],
    [undefined, null],
    [0, "0 B"],
    [999, "999 B"],
    [1000, "1.0 KB"],
    [31_400_000_000, "31.4 GB"],
    [2_500_000_000_000_000, "2500.0 TB"],
  ])("formats %s as %s", (bytes, expected) => {
    expect(formatBytes(bytes)).toBe(expected);
  });
});
