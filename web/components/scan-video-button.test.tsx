// @vitest-environment jsdom
/**
 * The 504 recovery path, which no helper test can reach.
 *
 * A slow encode outlives our request: the proxy returns 504 while the upstream
 * handler — synchronous, so a client disconnect doesn't cancel it — carries on
 * and writes the video. Nothing hands the browser that outcome, so the button
 * polls GET until the video appears. Before the poll existed, `pending` was
 * terminal and only a page reload recovered.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, render, screen } from "@testing-library/react";

import ScanVideoButton from "./scan-video-button";

function json(body: unknown, status: number) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

const VIDEO_URL = "https://signed.test/cyl-videos/5.mp4?token=a";

beforeEach(() => {
  vi.useFakeTimers();
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

async function clickGenerate() {
  await act(async () => {
    screen.getByRole("button").click();
  });
}

describe("ScanVideoButton after a 504", () => {
  it("recovers on its own once the encode lands", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(json({ detail: "still encoding" }, 504))
      .mockResolvedValue(json({ download_url: VIDEO_URL }, 200));
    vi.stubGlobal("fetch", fetchMock);

    render(<ScanVideoButton experimentId={1} scanId={5} />);
    await clickGenerate();

    // The POST timed out; no link yet.
    expect(screen.queryByRole("link")).toBeNull();
    expect(screen.getByRole("button").textContent).toContain("Still encoding");

    await act(async () => {
      await vi.advanceTimersByTimeAsync(10_000);
    });

    const link = screen.getByRole("link", { name: "Open video" });
    expect(link.getAttribute("href")).toBe(VIDEO_URL);
  });

  it("keeps waiting while the video is not there yet", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(json({ detail: "still encoding" }, 504))
      .mockResolvedValue(json({ detail: "not yet" }, 404));
    vi.stubGlobal("fetch", fetchMock);

    render(<ScanVideoButton experimentId={1} scanId={5} />);
    await clickGenerate();

    await act(async () => {
      await vi.advanceTimersByTimeAsync(30_000);
    });

    // Still polling, still no link — and not reported as a failure.
    expect(screen.queryByRole("link")).toBeNull();
    expect(screen.queryByRole("alert")).toBeNull();
    expect(screen.getByRole("button").textContent).toContain("Still encoding");
  });

  it("polls the scan's own video endpoint", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(json({ detail: "still encoding" }, 504))
      .mockResolvedValue(json({ download_url: VIDEO_URL }, 200));
    vi.stubGlobal("fetch", fetchMock);

    render(<ScanVideoButton experimentId={7} scanId={42} />);
    await clickGenerate();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(10_000);
    });

    expect(fetchMock.mock.calls[1][0]).toBe(
      "/api/cyl/experiments/7/scans/42/video"
    );
  });

  it("stops polling once unmounted", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(json({ detail: "still encoding" }, 504))
      .mockResolvedValue(json({ detail: "not yet" }, 404));
    vi.stubGlobal("fetch", fetchMock);

    const { unmount } = render(<ScanVideoButton experimentId={1} scanId={5} />);
    await clickGenerate();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(10_000);
    });

    const afterFirstPoll = fetchMock.mock.calls.length;
    unmount();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(60_000);
    });

    expect(fetchMock.mock.calls.length).toBe(afterFirstPoll);
  });
});

describe("ScanVideoButton when a video already exists", () => {
  it("offers only the link — a stored video is never regenerated", () => {
    render(
      <ScanVideoButton experimentId={1} scanId={5} initialVideoUrl={VIDEO_URL} />
    );

    expect(screen.getByRole("link", { name: "Open video" })).toBeTruthy();
    expect(screen.queryByRole("button")).toBeNull();
  });
});

describe("ScanVideoButton on a malformed success", () => {
  it("reports an error instead of freezing on a null body", async () => {
    // A 2xx whose body doesn't parse used to throw past setStatus, leaving the
    // button disabled on "Generating video…" forever.
    const fetchMock = vi
      .fn()
      .mockResolvedValue(new Response("<html>ok</html>", { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);

    render(<ScanVideoButton experimentId={1} scanId={5} />);
    await clickGenerate();

    expect(screen.getByRole("alert").textContent).toContain("unexpected");
    expect((screen.getByRole("button") as HTMLButtonElement).disabled).toBe(
      false
    );
  });
});
