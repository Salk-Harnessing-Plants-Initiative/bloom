// @vitest-environment jsdom
/** A step's log: loaded on request, with plain words when it can't be shown. */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";

import StepLog, { logMessage } from "./StepLog";

let fetchSpy: ReturnType<typeof vi.fn>;

function respond(body: unknown, status = 200) {
  fetchSpy.mockResolvedValue(new Response(JSON.stringify(body), { status }));
}

beforeEach(() => {
  fetchSpy = vi.fn();
  vi.stubGlobal("fetch", fetchSpy);
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("StepLog", () => {
  it("loads nothing until asked, then shows the log", async () => {
    respond({ step: "count", log: "Pipestance completed successfully!\n", truncated: false });
    render(<StepLog runId={3} step="count" running={false} />);
    expect(fetchSpy).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "View log" }));
    expect(await screen.findByText(/Pipestance completed successfully!/)).toBeTruthy();
    expect(fetchSpy.mock.calls[0][0]).toBe("/api/scrna/cellranger/runs/3/logs?step=count");
    expect(screen.queryByText(/may be cut/)).toBeNull();
  });

  it("says when only the end of a long log is shown", async () => {
    respond({ step: "count", log: "…", truncated: true });
    render(<StepLog runId={3} step="count" running={false} />);
    fireEvent.click(screen.getByRole("button", { name: "View log" }));
    expect(await screen.findByText("Showing the end of the log; it may be cut.")).toBeTruthy();
  });

  it("offers Refresh while the step runs, and fetches again", async () => {
    respond({ step: "count", log: "a", truncated: false });
    render(<StepLog runId={3} step="count" running />);
    fireEvent.click(screen.getByRole("button", { name: "View log" }));
    fireEvent.click(await screen.findByRole("button", { name: "Refresh" }));
    await screen.findByRole("button", { name: "Refresh" });
    expect(fetchSpy).toHaveBeenCalledTimes(2);
  });

  it("shows the service's reason when the log can't be read", async () => {
    respond({ detail: "The log of step count is no longer available; the workflow was removed from the cluster" }, 410);
    render(<StepLog runId={3} step="count" running={false} />);
    fireEvent.click(screen.getByRole("button", { name: "View log" }));
    expect((await screen.findByRole("status")).textContent).toMatch(/no longer available/);
  });

  it("goes back to the button on Hide", async () => {
    respond({ step: "count", log: "a", truncated: false });
    render(<StepLog runId={3} step="count" running={false} />);
    fireEvent.click(screen.getByRole("button", { name: "View log" }));
    fireEvent.click(await screen.findByRole("button", { name: "Hide" }));
    expect(screen.getByRole("button", { name: "View log" })).toBeTruthy();
  });
});

describe("logMessage", () => {
  it.each([
    [503, null, "Bloom can't read logs from the cluster here; ask the Bloom admins."],
    [
      502,
      null,
      "Couldn't read the log from the cluster. The run itself isn't affected; if this keeps happening, ask the Bloom admins.",
    ],
    [401, null, "Sign in to see logs."],
    [500, null, "Couldn't load this log. Try again shortly."],
    [409, "Step count hasn't started running yet", "Step count hasn't started running yet"],
  ])("says what %i means", (status, detail, message) => {
    expect(logMessage(status, detail)).toBe(message);
  });
});
