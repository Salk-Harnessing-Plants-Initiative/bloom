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

async function click(name: string) {
  await act(async () => {
    screen.getByRole("button", { name }).click();
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

describe("ScanVideoButton when the poll runs out of patience", () => {
  it("stops offering Generate, so a second encode can't start", async () => {
    // The encode is still running upstream; re-offering the button here is
    // exactly how one scan ends up with two concurrent encodes.
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(json({ detail: "still encoding" }, 504))
      .mockResolvedValue(json({ detail: "not yet" }, 404));
    vi.stubGlobal("fetch", fetchMock);

    render(<ScanVideoButton experimentId={1} scanId={5} />);
    await clickGenerate();

    await act(async () => {
      await vi.advanceTimersByTimeAsync(11 * 60_000);
    });

    expect(screen.queryByRole("button")).toBeNull();
    expect(screen.getByText(/taking longer than expected/)).toBeTruthy();

    // And it has genuinely stopped asking.
    const settled = fetchMock.mock.calls.length;
    await act(async () => {
      await vi.advanceTimersByTimeAsync(60_000);
    });
    expect(fetchMock.mock.calls.length).toBe(settled);
  });

  it("measures elapsed time, not completed polls", async () => {
    // Every poll answers slowly, so a tick-counting budget would never reach
    // the limit and the give-up would never fire.
    const fetchMock = vi.fn().mockImplementation((_url, init) => {
      if (init?.method === "POST") {
        return Promise.resolve(json({ detail: "still encoding" }, 504));
      }
      return new Promise((resolve) =>
        setTimeout(() => resolve(json({ detail: "not yet" }, 404)), 9_000)
      );
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<ScanVideoButton experimentId={1} scanId={5} />);
    await clickGenerate();

    await act(async () => {
      await vi.advanceTimersByTimeAsync(11 * 60_000);
    });

    expect(screen.getByText(/taking longer than expected/)).toBeTruthy();
  });
});

describe("ScanVideoButton on a 409", () => {
  it("adopts the stored video instead of naming one it doesn't show", async () => {
    // Reachable from a second tab, or after the poll gave up and the encode
    // then landed. Telling the reader to "open the stored one" while showing
    // no link is a dead end.
    const fetchMock = vi.fn().mockImplementation((_url, init) => {
      if (init?.method === "POST") {
        return Promise.resolve(
          json({ detail: "This scan already has a video." }, 409)
        );
      }
      return Promise.resolve(json({ download_url: VIDEO_URL }, 200));
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<ScanVideoButton experimentId={1} scanId={5} />);
    await clickGenerate();

    const link = screen.getByRole("link", { name: "Open video" });
    expect(link.getAttribute("href")).toBe(VIDEO_URL);
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("still reports an error if the stored video can't be fetched either", async () => {
    const fetchMock = vi.fn().mockImplementation((_url, init) => {
      if (init?.method === "POST") {
        return Promise.resolve(json({ detail: "already has a video" }, 409));
      }
      return Promise.resolve(json({ detail: "nope" }, 404));
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<ScanVideoButton experimentId={1} scanId={5} />);
    await clickGenerate();

    expect(screen.getByRole("alert").textContent).toContain("already has a video");
  });
});

describe("ScanVideoButton when a video already exists", () => {
  it("offers the stored video and a regenerate, for once more frames have landed", () => {
    render(
      <ScanVideoButton experimentId={1} scanId={5} initialVideoUrl={VIDEO_URL} />
    );

    expect(screen.getByRole("link", { name: "Open video" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Regenerate video" })).toBeTruthy();
  });

  it("does not start the encode on the first click", async () => {
    // Upstream writes the same object, and the frames available now are not necessarily
    // the frames the stored video holds — so replacing one is not a one-click action.
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);

    render(
      <ScanVideoButton experimentId={1} scanId={5} initialVideoUrl={VIDEO_URL} />
    );
    await click("Regenerate video");

    expect(fetchMock).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: "Regenerate anyway" })).toBeTruthy();
  });
});

describe("ScanVideoButton on a scan the page is calling incomplete", () => {
  const WARNING = "Showing 40 of 72 frames — 32 not available.";

  it("asks first, quoting what the viewer says", async () => {
    // The button and the frame viewer render as unrelated siblings, so nothing else stops
    // a user encoding a rotation the page is simultaneously flagging as partial.
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);

    render(
      <ScanVideoButton experimentId={1} scanId={5} completenessWarning={WARNING} />
    );
    await click("Generate video");

    expect(fetchMock).not.toHaveBeenCalled();
    expect(document.body.textContent).toContain("Showing 40 of 72 frames");
  });

  it("encodes once confirmed", async () => {
    const fetchMock = vi.fn().mockResolvedValue(json({ download_url: VIDEO_URL }, 200));
    vi.stubGlobal("fetch", fetchMock);

    render(
      <ScanVideoButton experimentId={1} scanId={5} completenessWarning={WARNING} />
    );
    await click("Generate video");
    await click("Generate anyway");

    expect(fetchMock).toHaveBeenCalled();
    expect(fetchMock.mock.calls[0][1]).toMatchObject({ method: "POST" });
  });

  it("goes straight to encoding when the scan looks whole", async () => {
    const fetchMock = vi.fn().mockResolvedValue(json({ download_url: VIDEO_URL }, 200));
    vi.stubGlobal("fetch", fetchMock);

    render(<ScanVideoButton experimentId={1} scanId={5} />);
    await clickGenerate();

    expect(fetchMock).toHaveBeenCalled();
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
