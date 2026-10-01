// @vitest-environment jsdom
/**
 * The page-wide record of run submissions (PR 6 review): one POST per target
 * however often its dialog is closed, reopened or unmounted.
 */

import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { act, cleanup, render, screen } from "@testing-library/react";
import { beginSubmission, resetSubmissions, settleSubmission, submissionKey, useSubmission } from "./submissions";

beforeEach(() => resetSubmissions());
afterEach(() => cleanup());

describe("submissionKey", () => {
  it("names a single target by level and id", () => {
    expect(submissionKey({ target_level: "wave", target_id: 11 })).toBe("wave:11");
    expect(submissionKey({ target_level: "scan", target_id: 11 })).toBe("scan:11");
  });

  it("names a selection by its sorted, distinct ids, so order and repeats don't make a new target", () => {
    expect(submissionKey({ target_level: "scan_ids", scan_ids: [9, 2, 6, 2] })).toBe(
      submissionKey({ target_level: "scan_ids", scan_ids: [2, 6, 9] }),
    );
    expect(submissionKey({ target_level: "scan_ids", scan_ids: [2, 6] })).not.toBe(
      submissionKey({ target_level: "scan_ids", scan_ids: [2, 6, 9] }),
    );
  });
});

describe("beginSubmission", () => {
  it("lets one submission through per target while it is sending, started or uncertain", () => {
    expect(beginSubmission("wave:11")).toBe(true);
    expect(beginSubmission("wave:11")).toBe(false);
    expect(beginSubmission("wave:12")).toBe(true);
    settleSubmission("wave:11", { kind: "uncertain" });
    expect(beginSubmission("wave:11")).toBe(false);
    settleSubmission("wave:12", { kind: "started", runId: 91, scanCount: 40 });
    expect(beginSubmission("wave:12")).toBe(false);
  });

  it("allows another try once a submission is settled as refused (null)", () => {
    expect(beginSubmission("wave:11")).toBe(true);
    settleSubmission("wave:11", null);
    expect(beginSubmission("wave:11")).toBe(true);
  });
});

describe("useSubmission", () => {
  function Show({ k }: { k: string }) {
    const s = useSubmission(k);
    return <div data-testid="s">{s ? s.kind : "none"}</div>;
  }

  it("re-renders as the target's submission changes", () => {
    render(<Show k="wave:11" />);
    expect(screen.getByTestId("s").textContent).toBe("none");
    act(() => void beginSubmission("wave:11"));
    expect(screen.getByTestId("s").textContent).toBe("sending");
    act(() => settleSubmission("wave:11", { kind: "started", runId: 91, scanCount: 40 }));
    expect(screen.getByTestId("s").textContent).toBe("started");
    act(() => settleSubmission("wave:11", null));
    expect(screen.getByTestId("s").textContent).toBe("none");
  });
});
