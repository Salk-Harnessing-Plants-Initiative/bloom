/** Counts-first run display state: the spec's "Run display state is derived from counts first". */

import { describe, expect, it } from "vitest";
import { RUN_STATUSES, runDisplay, SCAN_STATUSES, scanStatusLabel, type RunCounts } from "./run-display";

function run(overrides: Partial<RunCounts> & { completed_at?: string | null; reused_count?: number }): RunCounts {
  return { status: "running", scan_count: 40, done_count: 0, failed_count: 0, error_message: null, ...overrides };
}

describe("runDisplay", () => {
  it.each([
    // spec scenarios
    ["complete with failures", { status: "complete", done_count: 37, failed_count: 3 }, "Finished · 37 succeeded · 3 failed"],
    ["complete with scans that have no result", { status: "complete", done_count: 30, failed_count: 2 }, "Ended · 30 succeeded · 2 failed · 8 without a result"],
    ["partial with settled counts is finished", { status: "partial", scan_count: 50, done_count: 25, failed_count: 25 }, "Finished · 25 succeeded · 25 failed"],
    ["partial with unsettled counts is Partial, never Running", { status: "partial", scan_count: 50, done_count: 20, failed_count: 25 }, "Partial · 20 / 50 succeeded · 25 failed"],
    ["zero-scan run", { status: "complete", scan_count: 0 }, "No scans matched"],
    ["over-count keeps failures and clamps successes", { status: "running", scan_count: 10, done_count: 9, failed_count: 3 }, "Finished · 7 succeeded · 3 failed"],
    // the remaining rules
    ["all succeeded", { status: "complete", done_count: 40 }, "Finished · 40 succeeded"],
    ["failed with scans that have no result", { status: "failed", done_count: 5, failed_count: 2 }, "Failed · 5 succeeded · 2 failed · 33 without a result"],
    ["queued", { status: "queued" }, "Queued · 0 / 40 succeeded"],
    ["submitted", { status: "submitted", done_count: 3 }, "Submitted · 3 / 40 succeeded"],
    ["running with failures", { status: "running", done_count: 10, failed_count: 2 }, "Running · 10 / 40 succeeded · 2 failed"],
    ["an unknown status", { status: "paused", done_count: 3 }, "paused · 3 / 40 succeeded"],
    ["failed count above scan count", { status: "failed", scan_count: 4, done_count: 2, failed_count: 9 }, "Finished · 0 succeeded · 4 failed"],
  ])("%s", (_name, overrides, label) => {
    expect(runDisplay(run(overrides)).label).toBe(label);
  });

  it("keeps a failed run's error message, whichever rule applies", () => {
    const display = runDisplay(
      run({ status: "failed", done_count: 0, failed_count: 40, error_message: "dispatch rejected" }),
    );
    expect(display.label).toBe("Finished · 0 succeeded · 40 failed");
    expect(display.errorMessage).toBe("dispatch rejected");
  });

  it("shows no error message for a status other than failed", () => {
    expect(runDisplay(run({ status: "complete", error_message: "stale text" })).errorMessage).toBeNull();
  });

  it("shows no error message for a failed run whose message is empty", () => {
    expect(runDisplay(run({ status: "failed", error_message: "" })).errorMessage).toBeNull();
    expect(runDisplay(run({ status: "failed", error_message: undefined })).errorMessage).toBeNull();
  });

  it("ignores completed_at", () => {
    const display = runDisplay(run({ status: "running", completed_at: "2026-09-28T10:00:00+00:00", done_count: 10 }));
    expect(display.label).toBe("Running · 10 / 40 succeeded");
  });

  it("always carries the raw status as secondary text", () => {
    for (const status of [...RUN_STATUSES, "paused"]) {
      expect(runDisplay(run({ status })).rawStatus).toBe(status);
    }
    expect(runDisplay(run({ status: "complete", done_count: 40 })).rawStatus).toBe("complete");
  });

  it("gives each stage its own tooltip, and says a long queue may mean the dispatcher is down", () => {
    const tips = ["queued", "submitted", "running"].map((status) => runDisplay(run({ status })).tooltip);
    expect(new Set(tips).size).toBe(3);
    for (const tip of tips) expect(tip.length).toBeGreaterThan(0);
    expect(tips[0]).toMatch(/dispatcher/i);
  });

  it("gives every rule a tooltip", () => {
    for (const status of [...RUN_STATUSES, "paused"]) {
      for (const counts of [{}, { done_count: 40 }, { scan_count: 0 }]) {
        expect(runDisplay(run({ status, ...counts })).tooltip.length).toBeGreaterThan(0);
      }
    }
  });

  it("never mentions reused_count, even when it is set", () => {
    for (const status of [...RUN_STATUSES, "paused"]) {
      const display = runDisplay(run({ status, reused_count: 919, done_count: 3 }));
      const text = [display.label, display.tooltip, display.rawStatus, display.errorMessage].join(" ");
      expect(text).not.toMatch(/reus/i);
      expect(text).not.toContain("919");
    }
  });

  it("reports the clamped counts", () => {
    expect(runDisplay(run({ scan_count: 10, done_count: 9, failed_count: 3 })).counts).toEqual({ N: 10, D: 7, F: 3, U: 0 });
  });
});

describe("scanStatusLabel", () => {
  it.each([
    ["queued", "Waiting"],
    ["predicted", "Waiting"],
    ["written", "Result recorded"],
    ["reused", "Result recorded"],
    ["failed", "Failed"],
  ])("labels %s as %s, keeping the raw value", (status, label) => {
    expect(scanStatusLabel(status)).toEqual({ label, raw: status });
  });

  it("covers every CHECK value", () => {
    expect([...SCAN_STATUSES].sort()).toEqual(["failed", "predicted", "queued", "reused", "written"]);
  });

  it("shows an unknown value raw", () => {
    expect(scanStatusLabel("mystery")).toEqual({ label: "mystery", raw: "mystery" });
  });
});
