// @vitest-environment jsdom
/** A run action: it opens the shared confirm dialog, and a selection over the limit is disabled (task 11.2). */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import type { RunPipelineDialogProps } from "./RunPipelineDialog";
import { StartedRunsProvider, useStartedRuns, type StartedRun } from "./started-runs";

const dialog = vi.hoisted(() => ({ props: null as RunPipelineDialogProps | null }));
vi.mock("./RunPipelineDialog", () => ({
  RunPipelineDialog: (props: RunPipelineDialogProps) => {
    dialog.props = props;
    return <div role="dialog">{props.title}</div>;
  },
}));

import { RunPipelineButton } from "./RunPipelineButton";

const range = (n: number) => Array.from({ length: n }, (_, i) => i + 1);
const started: StartedRun = {
  pipeline_run_id: 91,
  scan_count: 40,
  target: { target_level: "experiment", target_id: 5 },
  requested_by: null,
  started_at: "2026-09-29T12:00:00.000Z",
};

beforeEach(() => {
  dialog.props = null;
});
afterEach(() => cleanup());

describe("RunPipelineButton", () => {
  it("opens the dialog for its target, and closes it", () => {
    render(<RunPipelineButton target={{ target_level: "experiment", target_id: 5 }} label="Run experiment" title="experiment Exp five" />);
    expect(screen.queryByRole("dialog")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Run experiment" }));
    expect(screen.getByRole("dialog").textContent).toBe("experiment Exp five");
    expect(dialog.props!.target).toEqual({ target_level: "experiment", target_id: 5 });
    act(() => dialog.props!.onClose());
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("is enabled at exactly MAX_TRIGGER_SCAN_IDS selected scans", () => {
    render(<RunPipelineButton target={{ target_level: "scan_ids", scan_ids: range(5000) }} label="Run selected (5000)" title="5000 selected scans" />);
    expect((screen.getByRole("button", { name: "Run selected (5000)" }) as HTMLButtonElement).disabled).toBe(false);
  });

  it("is disabled over MAX_TRIGGER_SCAN_IDS, and says why", () => {
    render(<RunPipelineButton target={{ target_level: "scan_ids", scan_ids: range(5001) }} label="Run selected (5001)" title="5001 selected scans" />);
    const button = screen.getByRole("button", { name: "Run selected (5001)" }) as HTMLButtonElement;
    expect(button.disabled).toBe(true);
    expect(screen.getByText(/at most 5000/).textContent).toContain("5001");
    fireEvent.click(button);
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("shows its note", () => {
    render(<RunPipelineButton target={{ target_level: "scan", target_id: 1 }} label="Run this scan" title="scan 1" note="careful" />);
    expect(screen.getByText("careful")).toBeTruthy();
  });

  it("announces a started run to the page", () => {
    const heard: StartedRun[] = [];
    function Listener() {
      useStartedRuns((run) => heard.push(run));
      return null;
    }
    render(
      <StartedRunsProvider>
        <Listener />
        <RunPipelineButton target={{ target_level: "experiment", target_id: 5 }} label="Run experiment" title="experiment Exp five" />
      </StartedRunsProvider>,
    );
    fireEvent.click(screen.getByRole("button", { name: "Run experiment" }));
    act(() => dialog.props!.onStarted!(started));
    expect(heard).toEqual([started]);
  });
});
