import { describe, expect, it } from "vitest";
import { MAX_SRA_RUNS, newSampleNameProblem, parseSraRuns } from "./sra-runs";

describe("parseSraRuns", () => {
  it.each([
    ["SRR28503597", ["SRR28503597"]],
    ["SRR28503597\nSRR28503598", ["SRR28503597", "SRR28503598"]],
    ["SRR28503597, SRR28503598", ["SRR28503597", "SRR28503598"]],
    [
      "  SRR28503597\r\n\tERR1000001 ,DRR1000002\n",
      ["SRR28503597", "ERR1000001", "DRR1000002"],
    ],
  ])("reads %j in order", (text, runs) => {
    expect(parseSraRuns(text)).toEqual({ runs, problem: null });
  });

  it("needs at least one run", () => {
    expect(parseSraRuns("  \n ").problem).toBe("Paste at least one SRA run ID.");
  });

  it.each([
    ["GSE262840", "a GEO series"],
    ["GSM8180251", "a GEO sample"],
    ["SRP123456", "an SRA study"],
    ["PRJNA123456", "a BioProject"],
    ["SRX1234567", "an SRA experiment"],
    ["SRS1234567", "an SRA sample"],
    ["SAMN12345678", "a BioSample"],
  ])("says what %s is and where to find its runs", (id, kind) => {
    const { problem } = parseSraRuns(`SRR28503597 ${id}`);
    expect(problem).toBe(
      `${id} is ${kind}, not a run. Find its runs (SRR…) in SRA's Run Selector.`,
    );
  });

  it.each(["SRR12", "SRR12345678901", "srr1234567", "SRR12345a7", "Col-0"])(
    "refuses %s",
    (id) => {
      expect(parseSraRuns(id).problem).toBe(
        `${id} isn't an SRA run ID like SRR12046049.`,
      );
    },
  );

  it("refuses a repeated run", () => {
    expect(parseSraRuns("SRR1000001,SRR1000002,SRR1000001").problem).toBe(
      "SRR1000001 is listed twice.",
    );
  });

  it("allows 9 runs and refuses 10", () => {
    const runs = Array.from({ length: MAX_SRA_RUNS + 1 }, (_, i) => `SRR100000${i}`);
    expect(parseSraRuns(runs.slice(0, MAX_SRA_RUNS).join("\n")).problem).toBeNull();
    expect(parseSraRuns(runs.join("\n")).problem).toBe(
      "One sample can have at most 9 runs; this lists 10.",
    );
  });
});

describe("newSampleNameProblem", () => {
  it.each(["col0_root_tip_rep1", "SRR28503597", "a-b"])("accepts %s", (name) => {
    expect(newSampleNameProblem(name, [])).toBeNull();
  });

  it.each(["", "_x", "a__b", "a b", "a.b", "x".repeat(65)])("refuses %j", (name) => {
    expect(newSampleNameProblem(name, [])).not.toBeNull();
  });

  it("refuses a registered name", () => {
    expect(newSampleNameProblem("tinygex", ["tinygex"])).toBe(
      "tinygex is already a registered sample; choose another name.",
    );
  });
});
