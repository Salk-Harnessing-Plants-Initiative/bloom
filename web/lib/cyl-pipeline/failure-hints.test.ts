/**
 * Failed-row hints in the drill-down: a likely cause from the scan's metadata,
 * and the narrow bloom#900 no-op note.
 */

import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import { BACKSTOP_MESSAGE, isNoOpCandidate, likelyCause, NO_OP_NOTE } from "./failure-hints";
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

  it("is true only for the backstop text on a scan with results", () => {
    expect(isNoOpCandidate(failed(BACKSTOP_MESSAGE), true)).toBe(true);
    expect(isNoOpCandidate(failed(BACKSTOP_MESSAGE), false)).toBe(false);
    expect(isNoOpCandidate(failed("stage-in: species missing"), true)).toBe(false);
    expect(isNoOpCandidate(failed(null), true)).toBe(false);
    expect(isNoOpCandidate(failed(`${BACKSTOP_MESSAGE}.`), true)).toBe(false);
  });

  it("is false for a row that is not failed", () => {
    expect(isNoOpCandidate({ status: "written", error_message: BACKSTOP_MESSAGE }, true)).toBe(false);
  });

  it("carries the spec's note text", () => {
    expect(NO_OP_NOTE).toBe(
      "If this scan already had results before this run, this may be an unrecognised no-op re-delivery, which re-running won't change (bloom#900), or its result may have arrived after the run closed. Check the scan's traits before re-running.",
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
