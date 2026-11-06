// @vitest-environment jsdom
/** The poll both video buttons share: when it asks, and when it gives up. */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, renderHook } from "@testing-library/react";
import { useVideoJobPoll } from "./use-video-job-poll";
import { VIDEO_JOB_MAX_FAILED_POLLS, VIDEO_JOB_POLL_INTERVAL_MS } from "@/lib/video-job";

const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });

async function tick(times = 1) {
  for (let n = 0; n < times; n += 1) {
    await act(async () => {
      await vi.advanceTimersByTimeAsync(VIDEO_JOB_POLL_INTERVAL_MS);
    });
  }
}

function poll(answers: Array<() => Response | Promise<Response>>, onAnswer = () => true) {
  let i = 0;
  const fetchMock = vi.fn(async () => answers[Math.min(i++, answers.length - 1)]());
  vi.stubGlobal("fetch", fetchMock);
  const answered = vi.fn(onAnswer);
  const lost = vi.fn();
  const view = renderHook(() => useVideoJobPoll("/poll", true, answered, lost));
  return { fetchMock, answered, lost, view };
}

beforeEach(() => vi.useFakeTimers());
afterEach(() => {
  vi.useRealTimers();
  cleanup();
  vi.unstubAllGlobals();
});

describe("useVideoJobPoll", () => {
  it("does not ask again while an earlier ask is still out", async () => {
    let release!: (r: Response) => void;
    const { fetchMock } = poll([() => new Promise<Response>((r) => (release = r))]);
    await tick(3);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    await act(async () => release(json({ job: null })));
  });

  it("drops an answer that lands after the page has gone", async () => {
    let release!: (r: Response) => void;
    const { answered, view } = poll([() => new Promise<Response>((r) => (release = r))]);
    await tick();
    view.unmount();
    await act(async () => release(json({ job: {} })));
    expect(answered).not.toHaveBeenCalled();
  });

  it("counts an ask that throws as a failed one", async () => {
    const { lost } = poll([
      () => {
        throw new TypeError("network down");
      },
    ]);
    await tick(VIDEO_JOB_MAX_FAILED_POLLS);
    expect(lost).toHaveBeenCalledWith("unreachable");
  });

  it("keeps asking after one 401, which a token refresh can cause", async () => {
    const { lost, answered } = poll([() => json({}, 401), () => json({ job: {} })]);
    await tick(2);
    expect(lost).not.toHaveBeenCalled();
    expect(answered).toHaveBeenCalled();
  });

  it("stops as signed out on two 401s in a row, and asks no more", async () => {
    const { lost, fetchMock } = poll([() => json({}, 401)]);
    await tick(2);
    expect(lost).toHaveBeenCalledWith("signed_out");
    const asked = fetchMock.mock.calls.length;
    await tick(3);
    expect(fetchMock.mock.calls.length).toBe(asked);
  });

  it("stops asking once it has lost track", async () => {
    const { lost, fetchMock } = poll([() => json({}, 503)]);
    await tick(VIDEO_JOB_MAX_FAILED_POLLS);
    expect(lost).toHaveBeenCalledTimes(1);
    const asked = fetchMock.mock.calls.length;
    await tick(3);
    expect(fetchMock.mock.calls.length).toBe(asked);
  });
});
