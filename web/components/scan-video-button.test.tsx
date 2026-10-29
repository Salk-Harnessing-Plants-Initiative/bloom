// @vitest-environment jsdom
/**
 * The button queues a render, then polls the scan's job until it settles.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, render, screen, within } from "@testing-library/react";

const push = vi.hoisted(() => vi.fn());
vi.mock("next/navigation", () => ({ useRouter: () => ({ push }) }));

import ScanVideoButton from "./scan-video-button";
import {
  POLL_LOST,
  POLL_SIGNED_OUT,
  VIDEO_JOB_MAX_FAILED_POLLS,
  VIDEO_JOB_POLL_INTERVAL_MS,
  VIDEO_JOB_POLL_TIMEOUT_MS,
  type VideoJob,
} from "@/lib/video-job";

function json(body: unknown, status: number) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

const VIDEO_URL = "https://signed.test/cyl-videos/5.mp4?token=a";
const JOB_ID = "1a2b3c4d-0000-0000-0000-000000000000";
const QUEUED = { job_id: JOB_ID, created: true };

function job(overrides: Partial<VideoJob> = {}): VideoJob {
  return {
    id: JOB_ID,
    status: "queued",
    stage: null,
    frames_done: null,
    frames_total: null,
    error_code: null,
    queued_ahead: 0,
    created_at: "2026-10-29T10:00:00Z",
    updated_at: "2026-10-29T10:00:00Z",
    ...overrides,
  };
}

const RENDERED = job({ status: "rendered", frames_done: 72, frames_total: 72 });

/** POSTs answer `post`; GETs take `polls` in order, repeating the last. */
function serve(post: () => Response, polls: Array<() => Response>) {
  let i = 0;
  const fetchMock = vi.fn(async (_url: string, init?: RequestInit) => {
    if (init?.method === "POST") return post();
    const answer = polls[Math.min(i, polls.length - 1)];
    i += 1;
    return answer();
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

const poll = (download_url: string | null, j: VideoJob | null) => () =>
  json({ download_url, job: j }, 200);

beforeEach(() => {
  vi.useFakeTimers();
});

afterEach(() => {
  // Real timers before `cleanup`, so effect teardown runs on them.
  vi.useRealTimers();
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

async function click(name: string) {
  await act(async () => {
    screen.getByRole("button", { name }).click();
  });
}

async function tick(times = 1) {
  for (let n = 0; n < times; n += 1) {
    await act(async () => {
      await vi.advanceTimersByTimeAsync(VIDEO_JOB_POLL_INTERVAL_MS);
    });
  }
}

async function mount(props: Partial<Parameters<typeof ScanVideoButton>[0]> = {}) {
  let view!: ReturnType<typeof render>;
  await act(async () => {
    view = render(<ScanVideoButton experimentId={1} scanId={5} {...props} />);
  });
  return view;
}

describe("ScanVideoButton queuing a render", () => {
  it("queues, waits, then shows the finished video", async () => {
    serve(() => json(QUEUED, 202), [
      poll(null, null),
      poll(null, job()),
      poll(VIDEO_URL, RENDERED),
    ]);

    await mount();
    await click("Generate video");
    expect(screen.queryByRole("link")).toBeNull();

    await tick(2);

    const link = screen.getByRole("link", { name: "Open video" });
    expect(link.getAttribute("href")).toBe(VIDEO_URL);
    expect(document.body.textContent).toContain("Encoded 72 frames");
  });

  it("says how many jobs are ahead while queued", async () => {
    serve(() => json(QUEUED, 202), [
      poll(null, null),
      poll(null, job({ queued_ahead: 2 })),
    ]);

    await mount();
    await click("Generate video");
    await tick();

    expect(document.body.textContent).toContain("Queued — 2 ahead");
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("counts frames while downloading", async () => {
    serve(() => json(QUEUED, 202), [
      poll(null, null),
      poll(
        null,
        job({ status: "rendering", stage: "downloading", frames_done: 30, frames_total: 72 })
      ),
    ]);

    await mount();
    await click("Generate video");
    await tick();

    expect(document.body.textContent).toContain("Downloading frame 30 of 72");
    expect(screen.getByRole("progressbar").getAttribute("aria-valuenow")).toBe("30");
  });

  it("shows the failure as text with a job reference, and offers Generate again", async () => {
    serve(() => json(QUEUED, 202), [
      poll(null, null),
      poll(null, job({ status: "failed", error_code: "no_images" })),
    ]);

    await mount();
    await click("Generate video");
    await tick();

    const alert = screen.getByRole("alert");
    expect(alert.textContent).toContain("no images");
    expect(alert.textContent).toContain("(job 1a2b3c4d)");
    expect(alert.innerHTML).not.toContain("<");
    expect(screen.getByRole("button", { name: "Generate video" })).toBeTruthy();
  });

  it("waits on a shared job just the same when someone else queued it", async () => {
    serve(() => json({ job_id: JOB_ID, created: false }, 202), [
      poll(null, null),
      poll(VIDEO_URL, RENDERED),
    ]);

    await mount();
    await click("Generate video");
    await tick();

    expect(screen.getByRole("link", { name: "Open video" })).toBeTruthy();
  });

  it("ignores an older job's outcome while waiting for its own", async () => {
    serve(() => json(QUEUED, 202), [
      poll(null, null),
      poll(null, job({ id: "ffffffff-old", status: "failed", error_code: "unexpected" })),
    ]);

    await mount();
    await click("Generate video");
    await tick();

    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("says the stored video was kept", async () => {
    serve(() => json(QUEUED, 202), [
      poll(null, null),
      poll(VIDEO_URL, job({ status: "kept", frames_done: 72, frames_total: 72 })),
    ]);

    await mount();
    await click("Generate video");
    await tick();

    expect(document.body.textContent).toContain("already has a video");
  });

  it("does not offer to generate again once a video is made", async () => {
    serve(() => json(QUEUED, 202), [poll(null, null), poll(VIDEO_URL, RENDERED)]);

    await mount();
    await click("Generate video");
    await tick();

    expect(screen.queryByRole("button", { name: "Generate video" })).toBeNull();
  });

  it("keeps waiting through a failed poll", async () => {
    serve(() => json(QUEUED, 202), [
      poll(null, null),
      () => json({ detail: "x" }, 503),
      poll(VIDEO_URL, RENDERED),
    ]);

    await mount();
    await click("Generate video");
    await tick();
    expect(screen.queryByRole("alert")).toBeNull();
    await tick();

    expect(screen.getByRole("link", { name: "Open video" })).toBeTruthy();
  });

  it("polls for its own job on the scan's endpoint, each ask bounded below the interval", async () => {
    expect(VIDEO_JOB_POLL_TIMEOUT_MS).toBeLessThan(VIDEO_JOB_POLL_INTERVAL_MS);
    const timeout = vi.spyOn(AbortSignal, "timeout");
    const fetchMock = serve(() => json(QUEUED, 202), [poll(null, null), poll(null, job())]);

    await mount();
    await click("Generate video");
    await tick();

    const last = fetchMock.mock.calls.at(-1)!;
    expect(last[0]).toBe(`/api/cyl/scans/5/video?job_id=${QUEUED.job_id}`);
    expect(timeout).toHaveBeenCalledWith(VIDEO_JOB_POLL_TIMEOUT_MS);
  });

  it("stops polling once unmounted", async () => {
    const fetchMock = serve(() => json(QUEUED, 202), [poll(null, null), poll(null, job())]);

    const view = await mount();
    await click("Generate video");
    view.unmount();
    const calls = fetchMock.mock.calls.length;
    await tick(3);

    expect(fetchMock.mock.calls.length).toBe(calls);
  });
});

describe("ScanVideoButton when someone else's render is running", () => {
  it("shows its progress on arrival, without a click", async () => {
    serve(() => json(QUEUED, 202), [
      poll(null, job({ status: "rendering", stage: "encoding" })),
      poll(VIDEO_URL, RENDERED),
    ]);

    await mount();

    expect(document.body.textContent).toContain("Encoding the video");
    expect(
      (screen.getByRole("button", { name: "Generating video…" }) as HTMLButtonElement).disabled
    ).toBe(true);

    await tick();
    expect(screen.getByRole("link", { name: "Open video" })).toBeTruthy();
  });

  it("does not wait on a settled job it finds on arrival", async () => {
    serve(() => json(QUEUED, 202), [
      poll(null, job({ status: "failed", error_code: "unexpected" })),
    ]);

    await mount();

    expect(screen.getByRole("button", { name: "Generate video" })).toBeTruthy();
    expect(screen.queryByRole("alert")).toBeNull();
  });
});

describe("ScanVideoButton on a refused request", () => {
  it("shows the stored video on a 409, rather than naming one it doesn't show", async () => {
    serve(() => json({ detail: "This scan already has a video." }, 409), [
      poll(null, null),
      poll(VIDEO_URL, null),
    ]);

    await mount();
    await click("Generate video");

    expect(screen.getByRole("link", { name: "Open video" }).getAttribute("href")).toBe(
      VIDEO_URL
    );
  });

  it("reports the 409's sentence if the stored video can't be found", async () => {
    serve(() => json({ detail: "This scan already has a video." }, 409), [
      poll(null, null),
    ]);

    await mount();
    await click("Generate video");

    expect(screen.getByRole("alert").textContent).toContain("already has a video");
  });

  it("reports a rate limit", async () => {
    serve(() => json({ detail: null }, 429), [poll(null, null)]);

    await mount();
    await click("Generate video");

    expect(screen.getByRole("alert").textContent).toContain("Too many video requests");
  });

  it("reports an error instead of freezing on an unreadable success", async () => {
    serve(() => new Response("<html>ok</html>", { status: 202 }), [poll(null, null)]);

    await mount();
    await click("Generate video");

    expect(screen.getByRole("alert").textContent).toContain("unexpected");
    expect(
      (screen.getByRole("button", { name: "Generate video" }) as HTMLButtonElement).disabled
    ).toBe(false);
  });

  it("reports an unreachable service", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (_u: string, init?: RequestInit) => {
        if (init?.method === "POST") throw new TypeError("fetch failed");
        return poll(null, null)();
      })
    );

    await mount();
    await click("Generate video");

    expect(screen.getByRole("alert").textContent).toContain("Could not reach");
  });
});

describe("ScanVideoButton when a video already exists", () => {
  it("offers only the link — a stored video is not regenerated", async () => {
    const fetchMock = serve(() => json(QUEUED, 202), [poll(VIDEO_URL, null)]);

    const { container } = await mount({ initialVideoUrl: VIDEO_URL });

    expect(within(container).getByRole("link", { name: "Open video" })).toBeTruthy();
    expect(within(container).queryByRole("button")).toBeNull();
    expect(fetchMock).not.toHaveBeenCalled();
  });
});

describe("ScanVideoButton on a scan the page is calling incomplete", () => {
  const WARNING = "Showing 40 of 72 frames — 32 not available.";

  it("asks first, quoting what the viewer says", async () => {
    const fetchMock = serve(() => json(QUEUED, 202), [poll(null, null)]);

    await mount({ completenessWarning: WARNING });
    await click("Generate video");

    expect(fetchMock.mock.calls.some(([, init]) => init?.method === "POST")).toBe(false);
    expect(document.body.textContent).toContain("Showing 40 of 72 frames");
  });

  it("queues once confirmed", async () => {
    const fetchMock = serve(() => json(QUEUED, 202), [poll(null, null), poll(VIDEO_URL, RENDERED)]);

    await mount({ completenessWarning: WARNING });
    await click("Generate video");
    await click("Generate anyway");
    await tick();

    expect(fetchMock.mock.calls.some(([, init]) => init?.method === "POST")).toBe(true);
    expect(screen.getByRole("link", { name: "Open video" })).toBeTruthy();
  });

  it("cancel closes the panel and offers Generate again", async () => {
    const fetchMock = serve(() => json(QUEUED, 202), [poll(null, null)]);

    await mount({ completenessWarning: WARNING });
    await click("Generate video");
    await click("Cancel");

    expect(screen.getByRole("button", { name: "Generate video" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Generate anyway" })).toBeNull();
    expect(fetchMock.mock.calls.some(([, init]) => init?.method === "POST")).toBe(false);
  });

  it("partial encodes say what was left out", async () => {
    serve(() => json(QUEUED, 202), [
      poll(null, null),
      poll(VIDEO_URL, job({ status: "rendered", frames_done: 60, frames_total: 72 })),
    ]);

    await mount();
    await click("Generate video");
    await tick();

    expect(document.body.textContent).toContain("Encoded 60 of 72 frames");
  });
});

describe("ScanVideoButton when a poll cannot follow its job", () => {
  it("finishes on its own job even after a newer one is queued for the scan", async () => {
    // Asked without a job id, the route would answer with the newer job.
    const newer = job({ id: "ffffffff-0000-0000-0000-000000000000", status: "queued" });
    let latest = 0;
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string, init?: RequestInit) => {
        if (init?.method === "POST") return json(QUEUED, 202);
        if (String(url).includes(`job_id=${JOB_ID}`))
          return json({ download_url: VIDEO_URL, job: RENDERED }, 200);
        // Nothing queued when the page opens; the newer job comes after the click.
        latest += 1;
        return json({ download_url: null, job: latest === 1 ? null : newer }, 200);
      })
    );

    await mount();
    await click("Generate video");
    await tick();

    expect(screen.getByRole("link", { name: "Open video" })).toBeTruthy();
  });

  it("says to sign in again when the session has expired, and stops asking", async () => {
    const fetchMock = serve(() => json(QUEUED, 202), [
      poll(null, null),
      () => json({ detail: "Sign in to view this video." }, 401),
    ]);

    await mount();
    await click("Generate video");
    await tick();
    const asked = fetchMock.mock.calls.length;
    await tick(3);

    expect(screen.getByRole("alert").textContent).toBe(POLL_SIGNED_OUT);
    expect(fetchMock.mock.calls.length).toBe(asked);
  });

  it("says it lost track after a run of failed asks, and stops asking", async () => {
    const fetchMock = serve(() => json(QUEUED, 202), [
      poll(null, null),
      () => json({ detail: "busy" }, 503),
    ]);

    await mount();
    await click("Generate video");
    await tick(VIDEO_JOB_MAX_FAILED_POLLS - 1);
    expect(screen.queryByRole("alert")).toBeNull();

    await tick();
    const asked = fetchMock.mock.calls.length;
    await tick(3);

    expect(screen.getByRole("alert").textContent).toBe(POLL_LOST);
    expect(fetchMock.mock.calls.length).toBe(asked);
  });

  it("keeps waiting through a few failed asks", async () => {
    serve(() => json(QUEUED, 202), [
      poll(null, null),
      () => json({ detail: "busy" }, 503),
      () => json({ detail: "busy" }, 503),
      poll(VIDEO_URL, RENDERED),
    ]);

    await mount();
    await click("Generate video");
    await tick(3);

    expect(screen.queryByRole("alert")).toBeNull();
    expect(screen.getByRole("link", { name: "Open video" })).toBeTruthy();
  });
});

describe("ScanVideoButton after queuing", () => {
  it("takes the visitor to the Video Jobs page, on their job", async () => {
    push.mockClear();
    serve(() => json(QUEUED, 202), [poll(null, null)]);

    await mount();
    await click("Generate video");

    expect(push).toHaveBeenCalledWith(`/app/video-jobs?job=${JOB_ID}`);
  });

  it("stays put when the request was refused", async () => {
    push.mockClear();
    serve(() => json({ detail: "Too many video requests." }, 429), [poll(null, null)]);

    await mount();
    await click("Generate video");

    expect(push).not.toHaveBeenCalled();
  });
});
