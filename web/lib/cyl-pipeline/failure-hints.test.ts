/**
 * Failed-row hints in the drill-down: a likely cause from the scan's metadata,
 * and the narrow bloom#900 no-op note.
 */

import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import {
  BACKSTOP_MESSAGE,
  DISPATCH_REFUSED_MESSAGES,
  failedScanCause,
  isNoOpCandidate,
  likelyCause,
  NO_OP_NOTE,
  WRITEBACK_NO_RESULT_MESSAGE,
} from "./failure-hints";
import { stageInProblems } from "./stage-in";

const meta = (species_name: string | null, plant_age_days: number | null) => ({ species_name, plant_age_days });

describe("stageInProblems", () => {
  it("finds nothing for a named species and a whole age", () => {
    expect(stageInProblems(meta("Pennycress", 14))).toEqual([]);
    expect(stageInProblems(meta(" pennycress ", 0))).toEqual([]);
  });

  it("flags a blank species", () => {
    for (const species of [null, "", "   "]) {
      expect(stageInProblems(meta(species, 14))).toEqual(["species-missing"]);
    }
  });

  it("flags a null or non-whole age", () => {
    expect(stageInProblems(meta("pennycress", null))).toEqual(["age-missing"]);
    expect(stageInProblems(meta("pennycress", 14.5))).toEqual(["age-not-whole"]);
  });

  it("reports every problem", () => {
    expect(stageInProblems(meta(" ", null))).toEqual(["species-missing", "age-missing"]);
  });
});

describe("likelyCause", () => {
  it("explains a null age", () => {
    expect(likelyCause(meta("pennycress", null))).toBe("Likely cause: plant age missing");
  });

  it("explains a non-whole age and a blank species", () => {
    expect(likelyCause(meta("pennycress", 14.5))).toBe("Likely cause: plant age is not a whole number");
    expect(likelyCause(meta("", 14))).toBe("Likely cause: species missing");
  });

  it("names every problem stageInProblems finds", () => {
    expect(likelyCause(meta(null, null))).toBe("Likely cause: species missing; plant age missing");
  });

  it("gives nothing when no rule applies, or without metadata", () => {
    expect(likelyCause(meta("pennycress", 14))).toBeNull();
    expect(likelyCause(undefined)).toBeNull();
  });
});

describe("isNoOpCandidate", () => {
  const failed = (error_message: string | null) => ({ status: "failed", error_message });

  it("is true for the backstop text only on a scan with results, and only for that exact text", () => {
    expect(isNoOpCandidate(failed(BACKSTOP_MESSAGE), true)).toBe(true);
    expect(isNoOpCandidate(failed(BACKSTOP_MESSAGE), false)).toBe(false);
    expect(isNoOpCandidate(failed("stage-in: species missing"), true)).toBe(false);
    expect(isNoOpCandidate(failed(null), true)).toBe(false);
    expect(isNoOpCandidate(failed(`${BACKSTOP_MESSAGE}.`), true)).toBe(false);
  });

  it("is true for write-back's no-result text on a scan with results: the #900 no-op re-delivery", () => {
    // Staging run 11 (2026-09-30): a re-run of a scan whose only source no run-scan row carries.
    expect(isNoOpCandidate(failed(WRITEBACK_NO_RESULT_MESSAGE), true)).toBe(true);
    // Staging run 10's poison scan got the same text with no results: a real failure, no note.
    expect(isNoOpCandidate(failed(WRITEBACK_NO_RESULT_MESSAGE), false)).toBe(false);
    expect(isNoOpCandidate(failed(`${WRITEBACK_NO_RESULT_MESSAGE}.`), true)).toBe(false);
  });

  it("is false for a row that is not failed", () => {
    expect(isNoOpCandidate({ status: "written", error_message: BACKSTOP_MESSAGE }, true)).toBe(false);
  });

  it("carries the spec's note text", () => {
    expect(NO_OP_NOTE).toBe(
      "This scan has pipeline results, but this row recorded none. Either its result arrived after the run closed, or, if the scan already had results before this run, this was an unrecognised no-op re-delivery, which re-running won't change (bloom#900). Check the scan's traits before re-running.",
    );
  });
});

describe("BACKSTOP_MESSAGE", () => {
  it("is the status poller's backstop text, read from status_poller.py", () => {
    const source = readFileSync(
      fileURLToPath(new URL("../../../services/workflows/status_poller.py", import.meta.url)),
      "utf8",
    );
    // The poller passes the text as adjacent Python string literals.
    const block = /"p_error_message":\s*\(((?:\s*"[^"\n]*")+)\s*\)/.exec(source);
    expect(block, "p_error_message literal not found in status_poller.py").not.toBeNull();
    const text = [...block![1].matchAll(/"([^"\n]*)"/g)].map((m) => m[1]).join("");
    expect(text.length).toBeGreaterThan(20);
    expect(BACKSTOP_MESSAGE).toBe(text);
  });
});

describe("WRITEBACK_NO_RESULT_MESSAGE", () => {
  it("is write-back's reconcile text, read from bloomctl's ingest.py", () => {
    const source = readFileSync(
      fileURLToPath(new URL("../../../bloomcli/src/bloomctl/cyl/ingest.py", import.meta.url)),
      "utf8",
    );
    const match = /^NO_RESULT_MESSAGE = "([^"\n]+)"$/m.exec(source);
    expect(match, "NO_RESULT_MESSAGE literal not found in ingest.py").not.toBeNull();
    expect(WRITEBACK_NO_RESULT_MESSAGE).toBe(match![1]);
    expect(WRITEBACK_NO_RESULT_MESSAGE).not.toBe(BACKSTOP_MESSAGE);
  });
});

describe("failedScanCause", () => {
  it("gives the metadata hint for an ordinary failure", () => {
    expect(failedScanCause("stage-in: species missing", meta(null, 14))).toBe("Likely cause: species missing");
    expect(failedScanCause(WRITEBACK_NO_RESULT_MESSAGE, meta("pennycress", null))).toBe("Likely cause: plant age missing");
  });

  it("gives nothing for a scan the dispatch worker refused, whatever its metadata", () => {
    // bloom#863: a refused scan never reached stage-in, so a missing species or
    // age didn't cause it, and saying so would misattribute the failure.
    for (const message of DISPATCH_REFUSED_MESSAGES) {
      expect(failedScanCause(message, meta(null, null))).toBeNull();
    }
  });
});

describe("DISPATCH_REFUSED_MESSAGES", () => {
  it("are the dispatch worker's refusal texts, read from dispatch_worker.py", () => {
    const source = readFileSync(
      fileURLToPath(new URL("../../../services/workflows/dispatch_worker.py", import.meta.url)),
      "utf8",
    );
    const block = /_REFUSAL_MESSAGES = \{([^}]*)\}/.exec(source);
    expect(block, "_REFUSAL_MESSAGES not found in dispatch_worker.py").not.toBeNull();
    const texts = [...block![1].matchAll(/"[a-z]+":\s*"([^"\n]+)"/g)].map((m) => m[1]);
    expect(texts).toHaveLength(2);
    expect([...DISPATCH_REFUSED_MESSAGES].sort()).toEqual([...texts].sort());
  });
});
