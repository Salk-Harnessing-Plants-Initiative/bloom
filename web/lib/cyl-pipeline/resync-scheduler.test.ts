/**
 * Resync on every SUBSCRIBED, leading plus trailing (design D2): the first
 * SUBSCRIBED refetches at once, and further SUBSCRIBEDs within 2 s of a
 * refetch's start collapse into exactly one more, when that window ends.
 */

import { afterEach, beforeEach, describe, expect, it, vi, type Mock } from "vitest";
import { createResyncScheduler, nextConnectionState, RESYNC_WINDOW_MS } from "./resync-scheduler";

let refetch: Mock<() => void>;
const times: number[] = [];

beforeEach(() => {
  vi.useFakeTimers();
  vi.setSystemTime(0);
  times.length = 0;
  refetch = vi.fn(() => {
    times.push(Date.now());
  });
});
afterEach(() => {
  vi.useRealTimers();
});

describe("createResyncScheduler", () => {
  it("uses a 2 s window", () => {
    expect(RESYNC_WINDOW_MS).toBe(2000);
  });

  it("refetches at once on the first SUBSCRIBED", () => {
    const s = createResyncScheduler(refetch);
    s.onStatus("SUBSCRIBED");
    expect(times).toEqual([0]);
  });

  it("does not refetch on other statuses", async () => {
    const s = createResyncScheduler(refetch);
    for (const status of ["CHANNEL_ERROR", "TIMED_OUT", "CLOSED"]) s.onStatus(status);
    await vi.advanceTimersByTimeAsync(10_000);
    expect(refetch).not.toHaveBeenCalled();
  });

  it("turns a reconnect 500 ms into the window into exactly one refetch, at 2000 ms", async () => {
    const s = createResyncScheduler(refetch);
    s.onStatus("SUBSCRIBED");
    await vi.advanceTimersByTimeAsync(500);
    s.onStatus("CLOSED");
    s.onStatus("SUBSCRIBED");
    expect(times).toEqual([0]);
    await vi.advanceTimersByTimeAsync(1499);
    expect(times).toEqual([0]);
    await vi.advanceTimersByTimeAsync(1);
    expect(times).toEqual([0, 2000]);
    await vi.advanceTimersByTimeAsync(10_000);
    expect(times).toEqual([0, 2000]);
  });

  it("collapses a burst of four transitions in the window into exactly one refetch", async () => {
    const s = createResyncScheduler(refetch);
    s.onStatus("SUBSCRIBED");
    for (const t of [200, 300, 400, 900]) {
      await vi.advanceTimersByTimeAsync(t - Date.now());
      s.onStatus("SUBSCRIBED");
    }
    await vi.advanceTimersByTimeAsync(10_000);
    expect(times).toEqual([0, 2000]);
  });

  it("refetches at once for a SUBSCRIBED after the window has closed", async () => {
    const s = createResyncScheduler(refetch);
    s.onStatus("SUBSCRIBED");
    await vi.advanceTimersByTimeAsync(5000);
    s.onStatus("SUBSCRIBED");
    expect(times).toEqual([0, 5000]);
  });

  it("opens a new window with the trailing refetch", async () => {
    const s = createResyncScheduler(refetch);
    s.onStatus("SUBSCRIBED");
    await vi.advanceTimersByTimeAsync(500);
    s.onStatus("SUBSCRIBED");
    await vi.advanceTimersByTimeAsync(2000); // t = 2500: the trailing refetch ran at 2000
    s.onStatus("SUBSCRIBED");
    await vi.advanceTimersByTimeAsync(10_000);
    expect(times).toEqual([0, 2000, 4000]);
  });

  it("refetches at once on a user refresh, and that refetch opens a window too", async () => {
    const s = createResyncScheduler(refetch);
    s.onStatus("SUBSCRIBED");
    await vi.advanceTimersByTimeAsync(3000);
    s.refetchNow(); // t = 3000
    await vi.advanceTimersByTimeAsync(500);
    s.onStatus("SUBSCRIBED"); // inside the refresh's window
    expect(times).toEqual([0, 3000]);
    await vi.advanceTimersByTimeAsync(1500);
    expect(times).toEqual([0, 3000, 5000]);
  });

  it("runs a user refresh at once even inside a window, and restarts the window from it", async () => {
    const s = createResyncScheduler(refetch);
    s.onStatus("SUBSCRIBED");
    await vi.advanceTimersByTimeAsync(500);
    s.onStatus("SUBSCRIBED"); // pending trailing refetch
    s.refetchNow(); // t = 500: the refresh covers it
    await vi.advanceTimersByTimeAsync(10_000);
    expect(times).toEqual([0, 500]);
  });

  it("does nothing after dispose", async () => {
    const s = createResyncScheduler(refetch);
    s.onStatus("SUBSCRIBED");
    s.onStatus("SUBSCRIBED");
    s.dispose();
    await vi.advanceTimersByTimeAsync(10_000);
    s.onStatus("SUBSCRIBED");
    expect(times).toEqual([0]);
  });
});

describe("nextConnectionState", () => {
  it("is live after SUBSCRIBED and offline after an error, timeout or close", () => {
    expect(nextConnectionState("connecting", "SUBSCRIBED")).toBe("live");
    for (const status of ["CHANNEL_ERROR", "TIMED_OUT", "CLOSED"]) {
      expect(nextConnectionState("live", status)).toBe("offline");
      expect(nextConnectionState("connecting", status)).toBe("offline");
    }
    expect(nextConnectionState("offline", "SUBSCRIBED")).toBe("live");
  });

  it("keeps its state for an unknown status", () => {
    expect(nextConnectionState("connecting", "JOINING")).toBe("connecting");
    expect(nextConnectionState("live", "JOINING")).toBe("live");
  });
});
