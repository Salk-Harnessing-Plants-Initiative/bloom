// @vitest-environment jsdom
/**
 * useLiveSync joins Realtime as the signed-in user. Found in task 8.6: on a
 * full page load the browser client's socket had no user token yet, so the
 * join carried only the anon key, RLS ran as anon, and every run event was
 * dropped without an error.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, render } from "@testing-library/react";
import { deferred, resetSupabaseMock, supabaseMock } from "./__fixtures__/supabase-mock";

vi.mock("@/lib/supabase/client", async () => (await import("./__fixtures__/supabase-mock")).clientModule);

import { useLiveSync } from "./use-live-sync";

function Probe() {
  useLiveSync<number>({
    topic: "probe",
    bindings: [{ table: "cyl_pipeline_runs", event: "UPDATE" }],
    initial: 0,
    snapshot: async () => 0,
    apply: (v) => v,
  });
  return null;
}

const flush = () => act(async () => {});

beforeEach(() => resetSupabaseMock());
afterEach(cleanup);

describe("useLiveSync auth", () => {
  it("hands Realtime the signed-in user's token before it joins", async () => {
    render(<Probe />);
    await flush();
    expect(supabaseMock.log).toEqual(["setAuth:user-token", `subscribe:${supabaseMock.channels[0].topic}`]);
  });

  it("still joins when signed out, without setting a token", async () => {
    supabaseMock.session = null;
    render(<Probe />);
    await flush();
    expect(supabaseMock.setAuth).not.toHaveBeenCalled();
    expect(supabaseMock.channels[0].subscribed).toBe(true);
  });

  it("never joins if unmounted before the session is read", async () => {
    const session = deferred<{ data: { session: { access_token: string } }; error: null }>();
    const client = (await import("./__fixtures__/supabase-mock")).mockClient;
    const spy = vi.spyOn(client.auth, "getSession").mockReturnValueOnce(session.promise);
    const view = render(<Probe />);
    view.unmount();
    await act(async () => session.resolve({ data: { session: { access_token: "late" } }, error: null }));
    expect(supabaseMock.channels[0].subscribed).toBe(false);
    expect(supabaseMock.log.filter((l) => l.startsWith("subscribe"))).toEqual([]);
    spy.mockRestore();
  });
});
