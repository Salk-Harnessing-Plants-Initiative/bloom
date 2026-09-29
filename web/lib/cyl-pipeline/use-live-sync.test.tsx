// @vitest-environment jsdom
/**
 * useLiveSync: joining Realtime as the signed-in user, and keeping
 * out-of-snapshot updates consistent with the fetch buffer.
 *
 * Found in task 8.6: on a full page load the browser client's socket had no
 * user token yet, so the join carried only the anon key, RLS ran as anon, and
 * every run event was dropped without an error.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, render } from "@testing-library/react";
import { deferred, resetSupabaseMock, supabaseMock } from "./__fixtures__/supabase-mock";

vi.mock("@/lib/supabase/client", async () => (await import("./__fixtures__/supabase-mock")).clientModule);

import { useLiveSync, type LiveSync } from "./use-live-sync";

let handle: LiveSync<number> | null = null;
let snapshot: () => Promise<number> = async () => 0;

function Probe() {
  handle = useLiveSync<number>({
    topic: "probe",
    bindings: [{ table: "cyl_pipeline_runs", event: "UPDATE" }],
    initial: 0,
    snapshot: () => snapshot(),
    apply: (v) => v,
  });
  return null;
}

const flush = () => act(async () => {});

beforeEach(() => {
  resetSupabaseMock();
  handle = null;
  snapshot = async () => 0;
});
afterEach(cleanup);

describe("useLiveSync auth", () => {
  it("has Realtime read the session token before it joins, leaving token refresh to the client", async () => {
    render(<Probe />);
    await flush();
    // No argument: realtime-js fetches the token through supabase-js's accessToken
    // callback and keeps refreshing it; an explicit token would be pinned.
    expect(supabaseMock.log).toEqual(["setAuth:undefined", `subscribe:${supabaseMock.channels[0].topic}`]);
    expect(supabaseMock.setAuth).toHaveBeenCalledWith(undefined);
  });

  it("still joins when setAuth fails, and says so on the console", async () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    supabaseMock.setAuth.mockRejectedValueOnce(new Error("no session"));
    render(<Probe />);
    await flush();
    expect(supabaseMock.channels[0].subscribed).toBe(true);
    expect(warn).toHaveBeenCalled();
    warn.mockRestore();
  });

  it("never joins if unmounted before auth is set", async () => {
    const auth = deferred<void>();
    supabaseMock.setAuth.mockReturnValueOnce(auth.promise);
    const view = render(<Probe />);
    view.unmount();
    await act(async () => auth.resolve());
    expect(supabaseMock.channels[0].subscribed).toBe(false);
    expect(supabaseMock.log.filter((l) => l.startsWith("subscribe"))).toEqual([]);
  });
});

describe("useLiveSync updates outside a snapshot", () => {
  it("applies an update at once when no fetch is in flight", async () => {
    render(<Probe />);
    await flush();
    await act(async () => handle!.update((v) => v + 3));
    expect(handle!.view).toBe(3);
  });

  it("replays an update made during a fetch on top of the snapshot, instead of losing it", async () => {
    render(<Probe />);
    await flush();
    const pending = deferred<number>();
    snapshot = () => pending.promise;
    await act(async () => handle!.refresh());
    await act(async () => handle!.update((v) => v + 10));
    await act(async () => pending.resolve(5));
    expect(handle!.view).toBe(15);
  });

  it("counts snapshots started, so a caller can tell one began after it", async () => {
    render(<Probe />);
    await flush();
    const before = handle!.generation();
    await act(async () => handle!.refresh());
    expect(handle!.generation()).toBe(before + 1);
  });
});
