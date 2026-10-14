/**
 * Failed-row hints in the drill-down: a likely cause from the scan's metadata,
 * and a result of this run that arrived after the row was closed.
 */

import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import {
  BACKSTOP_MESSAGE,
  REMOVED_WORKFLOW_MESSAGE,
  DISPATCH_REFUSED_MESSAGES,
  failedScanCause,
  lateResultNote,
  likelyCause,
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

describe("lateResultNote", () => {
  const text = "This run's result arrived after this row was closed: the scan's current traits are this run's (source 40).";

  it("names the source when a failed row's scan's latest source was written by this run", () => {
    expect(lateResultNote("failed", 40, 91, 91)).toBe(text);
  });

  it("is null for a row that is not failed", () => {
    expect(lateResultNote("written", 40, 91, 91)).toBeNull();
    expect(lateResultNote("queued", 40, 91, 91)).toBeNull();
  });

  it("is null when the latest source came from another run, from no run, or is unknown", () => {
    expect(lateResultNote("failed", 40, 7, 91)).toBeNull();
    expect(lateResultNote("failed", 40, null, 91)).toBeNull();
    expect(lateResultNote("failed", 40, undefined, 91)).toBeNull();
    expect(lateResultNote("failed", null, 91, 91)).toBeNull();
    expect(lateResultNote("failed", undefined, 91, 91)).toBeNull();
  });
});

/** A module-level constant in status_poller.py, written as adjacent Python string literals. */
function pollerMessage(name: string): string {
  const source = readFileSync(
    fileURLToPath(new URL("../../../services/workflows/status_poller.py", import.meta.url)),
    "utf8",
  );
  const block = new RegExp(`^${name} = \\(((?:\\s*"[^"\\n]*")+)\\s*\\)`, "m").exec(source);
  expect(block, `${name} literal not found in status_poller.py`).not.toBeNull();
  const text = [...block![1].matchAll(/"([^"\n]*)"/g)].map((m) => m[1]).join("");
  expect(text.length).toBeGreaterThan(20);
  return text;
}

describe("BACKSTOP_MESSAGE", () => {
  it("is the status poller's backstop text, read from status_poller.py", () => {
    expect(BACKSTOP_MESSAGE).toBe(pollerMessage("_BACKSTOP_MESSAGE"));
  });
});

describe("REMOVED_WORKFLOW_MESSAGE", () => {
  it("is the status poller's removed-workflow text, read from status_poller.py", () => {
    expect(REMOVED_WORKFLOW_MESSAGE).toBe(pollerMessage("_REMOVED_MESSAGE"));
    expect(REMOVED_WORKFLOW_MESSAGE).not.toBe(BACKSTOP_MESSAGE);
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
