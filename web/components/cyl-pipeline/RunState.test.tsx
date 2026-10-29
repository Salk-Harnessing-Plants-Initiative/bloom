// @vitest-environment jsdom
/**
 * A run's display state links its failed count to the drill-down's failed
 * filter only when some scans failed (bloom#955): "0 failed" leads nowhere.
 */

import { afterEach, describe, expect, it } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import type { RunCounts } from "@/lib/cyl-pipeline/run-display";
import { RunState } from "./RunState";

const HREF = "/app/cyl-pipeline-runs/91?status=failed";

function run(overrides: Partial<RunCounts>): RunCounts {
  return { status: "running", scan_count: 40, done_count: 0, failed_count: 0, error_message: null, ...overrides };
}

afterEach(cleanup);

describe("no link when nothing failed", () => {
  it.each([
    ["Failed", { status: "failed", scan_count: 3 }, "Failed · 0 succeeded · 0 failed · 3 without a result"],
    ["Ended", { status: "complete", done_count: 30 }, "Ended · 30 succeeded · 0 failed · 10 without a result"],
    ["Partial", { status: "partial", scan_count: 10, done_count: 7 }, "Partial · 7 / 10 succeeded · 0 failed"],
  ])("%s with F = 0", (_name, overrides, label) => {
    const { container } = render(<RunState run={run(overrides)} failedHref={HREF} />);
    expect(container.textContent).toContain(label);
    expect(screen.queryByRole("link")).toBeNull();
  });
});

describe("a link when some scans failed", () => {
  it.each([
    ["Finished", { status: "complete", done_count: 37, failed_count: 3 }, "3 failed"],
    ["Failed with scans that have no result", { status: "failed", failed_count: 5 }, "5 failed"],
  ])("%s", (_name, overrides, name) => {
    render(<RunState run={run(overrides)} failedHref={HREF} />);
    expect(screen.getByRole("link", { name }).getAttribute("href")).toBe(HREF);
  });

  it("links nothing without failedHref", () => {
    render(<RunState run={run({ status: "complete", done_count: 37, failed_count: 3 })} />);
    expect(screen.queryByRole("link")).toBeNull();
  });
});
