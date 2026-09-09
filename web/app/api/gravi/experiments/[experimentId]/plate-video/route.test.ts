// @vitest-environment node
/**
 * Unit tests for the plate time-lapse proxy and its poll.
 *
 * The validation cases are the point. `isValidPlateId` is unit-tested on its
 * own, but nothing proves the route calls it — and a plate id reaches an
 * upstream request body and a storage key. So each of those cases asserts
 * upstream was never reached, not merely that the status was 400.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { Session } from "@supabase/supabase-js";

import * as routeModule from "@/app/api/gravi/experiments/[experimentId]/plate-video/route";

vi.mock("@/lib/supabase/server", () => ({ getSession: vi.fn() }));
vi.mock("@/lib/supabase/plate-video", () => ({ getStoredPlateVideo: vi.fn() }));

import { getSession } from "@/lib/supabase/server";
import { getStoredPlateVideo } from "@/lib/supabase/plate-video";

const mockedGetSession = vi.mocked(getSession);

const GENERIC_SENTENCE =
  "This video could not be made right now. Try again shortly — if it keeps " +
  "happening, let the Bloom team know.";
const mockedStored = vi.mocked(getStoredPlateVideo);

const RESULT = {
  experiment_id: 12,
  plate_id: "P7",
  wave_number: 1,
  action: "rendered",
  reason: "no video stored; encoding 86 frames",
  object_path: "12/wave-1/P7.mp4",
  frames: 86,
  frames_unknown: false,
  coverage: null,
};

// A real session, not one field cast past the type checker: the route reads
// `access_token`, and a fixture shaped like the type is what keeps that honest.
const SESSION: Session = {
  access_token: "token",
  refresh_token: "refresh",
  expires_in: 3600,
  token_type: "bearer",
  user: {
    id: "00000000-0000-0000-0000-000000000001",
    aud: "authenticated",
    app_metadata: {},
    user_metadata: {},
    created_at: "2026-01-01T00:00:00.000Z",
  },
};

function post(body: unknown, experimentId = "12") {
  return routeModule.POST(
    new Request("http://localhost/api/gravi/experiments/12/plate-video", {
      method: "POST",
      body: typeof body === "string" ? body : JSON.stringify(body),
    }),
    { params: Promise.resolve({ experimentId }) }
  );
}

function get(query: string, experimentId = "12") {
  return routeModule.GET(
    new Request(`http://localhost/api/gravi/experiments/12/plate-video?${query}`),
    { params: Promise.resolve({ experimentId }) }
  );
}

function upstreamReturns(
  status: number,
  payload: unknown,
  headers?: Record<string, string>
) {
  return vi.fn().mockResolvedValue(
    new Response(JSON.stringify(payload), { status, headers })
  );
}

beforeEach(() => {
  // Call history survives restoreAllMocks, so a test asserting "never called"
  // would see the previous test's calls.
  vi.clearAllMocks();
  mockedGetSession.mockResolvedValue(SESSION);
  mockedStored.mockResolvedValue({ status: "absent" });
});

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  vi.unstubAllEnvs();
});

it("runs in the runtime these tests run in", () => {
  expect(routeModule.runtime).toBe("nodejs");
});

describe("POST", () => {
  it("passes the plate through and returns what was rendered", async () => {
    const fetchMock = upstreamReturns(200, RESULT);
    vi.stubGlobal("fetch", fetchMock);

    const res = await post({ plate_id: "P7", wave_number: 1 });

    expect(res.status).toBe(200);
    // Everything the service sent except `frames_unknown`, which the caller is
    // told as `frames: null` instead.
    const { frames_unknown: _unused, ...expected } = RESULT;
    expect(await res.json()).toEqual(expected);

    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("http://workflows:5100/gravi/experiments/12/plate-video");
    expect(JSON.parse(init.body)).toEqual({ plate_id: "P7", wave_number: 1 });
  });

  it("asks the service to make a video, as JSON", async () => {
    // The verb and the content type are what make this a render rather than a
    // read. Neither was pinned, so either could be changed without a test noticing.
    const fetchMock = upstreamReturns(200, RESULT);
    vi.stubGlobal("fetch", fetchMock);

    await post({ plate_id: "P7", wave_number: 1 });
    const init = fetchMock.mock.calls[0][1];

    expect(init.method).toBe("POST");
    expect(init.headers["Content-Type"]).toBe("application/json");
  });

  it("goes to the service the environment names", async () => {
    // Every other test runs with the variable unset, so only the compose default
    // was ever exercised and the setting could stop being read unnoticed.
    vi.stubEnv("WORKFLOWS_URL", "http://workflows.internal:9000");
    const fetchMock = upstreamReturns(200, RESULT);
    vi.stubGlobal("fetch", fetchMock);

    await post({ plate_id: "P7", wave_number: 1 });

    expect(fetchMock.mock.calls[0][0]).toBe(
      "http://workflows.internal:9000/gravi/experiments/12/plate-video"
    );
  });

  it("gives up before undici does, so a slow encode is our own 504", async () => {
    // Without this the wait runs to undici's 300s header timeout and surfaces as
    // an opaque UND_ERR, which is the answer the 504 branch exists to replace.
    const timeout = vi.spyOn(AbortSignal, "timeout");
    const fetchMock = upstreamReturns(200, RESULT);
    vi.stubGlobal("fetch", fetchMock);

    await post({ plate_id: "P7", wave_number: 1 });

    expect(timeout).toHaveBeenCalledWith(240_000);
    expect(fetchMock.mock.calls[0][1].signal).toBe(timeout.mock.results[0].value);
  });

  it("refuses a body that is not JSON without reaching upstream", async () => {
    const fetchMock = upstreamReturns(200, RESULT);
    vi.stubGlobal("fetch", fetchMock);

    const res = await post("{not json");

    expect(res.status).toBe(400);
    expect((await res.json()).detail).toBe("expected a JSON body");
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("treats an omitted wave as no wave", async () => {
    // A client that simply leaves the field out, rather than sending null.
    const fetchMock = upstreamReturns(200, RESULT);
    vi.stubGlobal("fetch", fetchMock);

    const res = await post({ plate_id: "P7" });

    expect(res.status).toBe(200);
    expect(JSON.parse(fetchMock.mock.calls[0][1].body).wave_number).toBeNull();
  });

  it("names the rule the plate id is actually held to", async () => {
    // The pattern requires an alphanumeric first character, so a leading dash or
    // underscore is refused. A message about dots leaves that caller stuck.
    const fetchMock = upstreamReturns(200, RESULT);
    vi.stubGlobal("fetch", fetchMock);

    for (const plateId of ["_A1", "-A1", ".A1"]) {
      const res = await post({ plate_id: plateId });
      expect(res.status).toBe(400);
      expect((await res.json()).detail).toContain("begin with a letter or digit");
    }
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("says one sentence when a passthrough status carries no readable detail", async () => {
    // The arm that decides an upstream body is unusable. A validation failure
    // sends a list, not a sentence, and 422 is on the passthrough list.
    const unreadable = [
      { detail: [{ loc: ["body", "plate_id"], ctx: { db: "postgresql://bloom:pw@db:5432" } }] },
      { detail: "   " },
      { message: "something happened" },
      {},
    ];

    for (const payload of unreadable) {
      vi.stubGlobal("fetch", upstreamReturns(422, payload));
      const { detail } = await (await post({ plate_id: "P7", wave_number: 1 })).json();

      expect(detail).toBe(GENERIC_SENTENCE);
      expect(detail).not.toContain("postgresql");
    }
  });

  it("logs a reply it cannot read, which the service never saw either", async () => {
    const error = vi.spyOn(console, "error").mockImplementation(() => {});
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(new Response("<html>502</html>", { status: 502 }))
    );

    await post({ plate_id: "P7", wave_number: 1 });

    expect(error).toHaveBeenCalled();
  });

  it("sends the token upstream rather than exposing it to the browser", async () => {
    const fetchMock = upstreamReturns(200, RESULT);
    vi.stubGlobal("fetch", fetchMock);

    await post({ plate_id: "P7", wave_number: 1 });
    expect(fetchMock.mock.calls[0][1].headers.Authorization).toBe("Bearer token");
  });

  it("does not refuse a plate that already has a video", async () => {
    // A plate keeps gaining captures, so a stored video is usually not wrong,
    // just short. Whether to re-render is the service's decision.
    mockedStored.mockResolvedValue({
      status: "present",
      url: "https://x/y.mp4",
      frames: 86,
    });
    const fetchMock = upstreamReturns(200, RESULT);
    vi.stubGlobal("fetch", fetchMock);

    expect((await post({ plate_id: "P7", wave_number: 1 })).status).toBe(200);
    expect(fetchMock).toHaveBeenCalled();
  });

  it.each([
    ["../secrets"],
    ["a/b"],
    [".hidden"],
    [""],
    ["P".repeat(65)],
    [null],
    [7],
  ])("refuses plate id %p without reaching upstream", async (plateId) => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);

    const res = await post({ plate_id: plateId, wave_number: 1 });

    expect(res.status).toBe(400);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it.each([[true], ["one"], [1.5], [-1]])(
    "refuses wave %p without reaching upstream",
    async (wave) => {
      const fetchMock = vi.fn();
      vi.stubGlobal("fetch", fetchMock);

      const res = await post({ plate_id: "P7", wave_number: wave });

      expect(res.status).toBe(400);
      expect(fetchMock).not.toHaveBeenCalled();
    }
  );

  it("accepts a plate with no wave", async () => {
    const fetchMock = upstreamReturns(200, RESULT);
    vi.stubGlobal("fetch", fetchMock);

    await post({ plate_id: "P7", wave_number: null });
    expect(JSON.parse(fetchMock.mock.calls[0][1].body).wave_number).toBeNull();
  });

  it("treats wave zero as a wave", async () => {
    // The scanner app sends 0 when none is set, so it arrives in practice.
    const fetchMock = upstreamReturns(200, RESULT);
    vi.stubGlobal("fetch", fetchMock);

    await post({ plate_id: "P7", wave_number: 0 });
    expect(JSON.parse(fetchMock.mock.calls[0][1].body).wave_number).toBe(0);
  });

  it("interpolates the parsed experiment id, not the raw param", async () => {
    // `parseId` accepts /^\d+$/, so "007" is valid and parses to 7. Swapping
    // the parsed integer for the raw param at the interpolation site would be
    // an SSRF that every other test in this file still passes.
    const fetchMock = upstreamReturns(200, RESULT);
    vi.stubGlobal("fetch", fetchMock);

    await post({ plate_id: "P7", wave_number: 1 }, "007");

    expect(fetchMock.mock.calls[0][0]).toBe(
      "http://workflows:5100/gravi/experiments/7/plate-video"
    );
  });

  it("refuses a bad experiment id without reaching upstream", async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);

    const res = await post({ plate_id: "P7" }, "1/../../health");

    expect(res.status).toBe(400);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("refuses a signed-out caller without reaching upstream", async () => {
    mockedGetSession.mockResolvedValue(null as never);
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);

    const res = await post({ plate_id: "P7", wave_number: 1 });

    expect(res.status).toBe(401);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("treats a session carrying no token as signed out", async () => {
    // What a failed refresh can leave behind. Accepted, the request would go
    // upstream as `Bearer undefined` and be refused there instead.
    mockedGetSession.mockResolvedValue({ access_token: "" } as never);
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);

    const res = await post({ plate_id: "P7", wave_number: 1 });

    expect(res.status).toBe(401);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("reports a timeout as still encoding, not as a failure", async () => {
    // The encode carries on upstream; telling the user it failed would have
    // them click Generate again and start a second one.
    const timeout = new DOMException("timed out", "TimeoutError");
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(timeout));

    const res = await post({ plate_id: "P7", wave_number: 1 });

    expect(res.status).toBe(504);
    expect((await res.json()).detail).toContain("Still encoding");
  });

  it("still explains itself on a second bad request", async () => {
    // A Response carries its body as a stream that is spent when it is sent, so
    // one built once and shared answered the first caller and left every later
    // one with an empty body. Every other bad-input test reads only the status,
    // which is why nothing noticed.
    const first = await post({ plate_id: "Plate 9" });
    const second = await post({ plate_id: "a/b" });

    expect(first.status).toBe(400);
    expect(second.status).toBe(400);
    expect((await first.json()).detail).toContain("plateId must be");
    expect((await second.json()).detail).toContain("plateId must be");
    expect(first).not.toBe(second);
  });

  it("still explains a bad wave on a second request", async () => {
    const first = await post({ plate_id: "P7", wave_number: -1 });
    const second = await post({ plate_id: "P7", wave_number: 1.5 });

    expect((await first.json()).detail).toContain("waveNumber must be");
    expect((await second.json()).detail).toContain("waveNumber must be");
  });

  it.each([[404], [413], [422], [429]])(
    "passes an upstream %i detail through, because it is written for the caller",
    async (status) => {
      vi.stubGlobal(
        "fetch",
        upstreamReturns(status, { detail: "this plate has no captures with an image" })
      );

      const res = await post({ plate_id: "P7", wave_number: 1 });

      expect(res.status).toBe(status);
      expect((await res.json()).detail).toContain("no captures");
    }
  );

  it("hands back the upstream Retry-After, so the button need not guess", async () => {
    // One plate encodes at a time, so a second click gets 429 as the ordinary
    // answer. Without the header the caller has no idea how long to wait.
    vi.stubGlobal(
      "fetch",
      upstreamReturns(
        429,
        { detail: "another plate video is already encoding; try again shortly" },
        { "Retry-After": "30" }
      )
    );

    const res = await post({ plate_id: "P7", wave_number: 1 });

    expect(res.status).toBe(429);
    expect(res.headers.get("Retry-After")).toBe("30");
    expect((await res.json()).detail).toContain("already encoding");
  });

  it("omits Retry-After when upstream sends none", async () => {
    vi.stubGlobal("fetch", upstreamReturns(404, { detail: "no captures" }));

    const res = await post({ plate_id: "P7", wave_number: 1 });

    expect(res.headers.get("Retry-After")).toBeNull();
  });

  it.each([[500], [502], [503]])(
    "suppresses an upstream %i detail, which names internal things",
    async (status) => {
      // 5xx details carry the internal gateway URL, the service account's env
      // key names, and object paths. 503 is the one that looks safe and is not:
      // the plate-video 503 is written to be read, but `auth.py` answers 503
      // with the auth client's error, and status cannot tell the two apart.
      vi.stubGlobal(
        "fetch",
        upstreamReturns(status, { detail: "http://kong:8000 rejected WORKFLOWS_SUPABASE_EMAIL" })
      );

      const res = await post({ plate_id: "P7", wave_number: 1 });
      const { detail } = await res.json();

      expect(res.status).toBe(status);
      // One sentence for every suppressed case, rather than nothing at all --
      // a null detail leaves the browser to invent wording per status.
      expect(detail).toContain("could not be made right now");
      expect(detail).not.toContain("kong");
      expect(detail).not.toContain("WORKFLOWS_");
    }
  );

  it("tells an expired session to sign in, not to wait", async () => {
    // Ordinary: a plate page left open past the session expiry. The generic
    // sentence would send a scientist to wait for something waiting cannot fix,
    // and to report it to a team who can do nothing about it.
    vi.stubGlobal("fetch", upstreamReturns(401, { detail: "Invalid or expired token" }));

    const res = await post({ plate_id: "P7", wave_number: 1 });
    const { detail } = await res.json();

    expect(res.status).toBe(401);
    expect(detail).toBe("Your session has expired. Sign in again.");
  });

  it("does not hand the service's own wording for a login to a scientist", async () => {
    vi.stubGlobal("fetch", upstreamReturns(401, { detail: "Bearer token required" }));

    const { detail } = await (await post({ plate_id: "P7", wave_number: 1 })).json();

    expect(detail).not.toContain("Bearer");
    expect(detail).not.toContain("token");
    expect(detail).not.toContain("Bloom team");
  });

  it("passes a rate limit through, though it is not about this plate", async () => {
    // 429 has two sources: this plate is already rendering, and the caller is
    // clicking too fast. Both are safe to read, which is the whole test for the
    // list -- not whether the message is about the plate.
    vi.stubGlobal(
      "fetch",
      upstreamReturns(429, { detail: "Rate limit exceeded (30/60s); retry later" }, {
        "Retry-After": "60",
      })
    );

    const res = await post({ plate_id: "P7", wave_number: 1 });

    expect((await res.json()).detail).toContain("Rate limit exceeded");
    expect(res.headers.get("Retry-After")).toBe("60");
  });

  it.each([[404], [413], [422]])(
    "passes an object key through on %i, which a signed-in user can read anyway",
    async (status) => {
      // Named deliberately: the key is not internal infrastructure, and the path
      // is often the most useful part of the message.
      vi.stubGlobal(
        "fetch",
        upstreamReturns(status, { detail: "12/wave-1/P7.mp4 could not be used" })
      );

      const { detail } = await (await post({ plate_id: "P7", wave_number: 1 })).json();

      expect(detail).toContain("12/wave-1/P7.mp4");
    }
  );

  it("says null when the service cannot say how many frames a kept video holds", async () => {
    // The service says this as zero with a flag beside it. Zero is a claim about
    // the video -- an empty one -- and a kept video is never empty. Null is the
    // same word the poll uses, so a caller reads one shape from both.
    vi.stubGlobal(
      "fetch",
      upstreamReturns(200, { ...RESULT, action: "keep", frames: 0, frames_unknown: true })
    );

    const body = await (await post({ plate_id: "P7", wave_number: 1 })).json();

    expect(body.frames).toBeNull();
    expect(body).not.toHaveProperty("frames_unknown");
  });

  it("keeps a real count, and never mentions the flag", async () => {
    vi.stubGlobal("fetch", upstreamReturns(200, RESULT));

    const body = await (await post({ plate_id: "P7", wave_number: 1 })).json();

    expect(body.frames).toBe(86);
    expect(body).not.toHaveProperty("frames_unknown");
  });

  it("reports the video service being unreachable without naming it", async () => {
    // The commonest real failure: the container is down, so `fetch` rejects
    // before any reply. The rejection message holds the internal address.
    vi.stubGlobal(
      "fetch",
      vi.fn().mockRejectedValue(
        new TypeError("fetch failed: connect ECONNREFUSED http://workflows:5100")
      )
    );

    const res = await post({ plate_id: "P7", wave_number: 1 });
    const { detail } = await res.json();

    expect(res.status).toBe(502);
    expect(detail).toBe("The video service is unavailable.");
    expect(detail).not.toContain("workflows");
    expect(detail).not.toContain("5100");
  });

  it("distinguishes a service that is down from one that is still working", async () => {
    // A timeout means the encode is still running upstream; a refused connection
    // means there is nothing to wait for. Told apart, the button waits or stops.
    const timeout = new DOMException("timed out", "TimeoutError");
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(timeout));

    expect((await post({ plate_id: "P7", wave_number: 1 })).status).toBe(504);

    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("fetch failed")));

    expect((await post({ plate_id: "P7", wave_number: 1 })).status).toBe(502);
  });

  it("answers a reply that is not JSON, rather than passing the page on", async () => {
    // A proxy or gateway in front of the service answers with an HTML error
    // page. Handed on, it reaches the browser as the body of a JSON API.
    const page = "<html><body>502 Bad Gateway — nginx/1.24.0 upstream workflows:5100</body></html>";
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(new Response(page, { status: 502 }))
    );

    const res = await post({ plate_id: "P7", wave_number: 1 });
    const { detail } = await res.json();

    expect(res.status).toBe(502);
    expect(detail).toContain("could not be made right now");
    expect(detail).not.toContain("nginx");
    expect(detail).not.toContain("workflows");
  });

  it("does not report success when a 200 body cannot be read", async () => {
    // A truncated or non-JSON 200 is not a rendered video. Answered 200, the
    // button would report a video that does not exist.
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(new Response("not json at all", { status: 200 }))
    );

    const res = await post({ plate_id: "P7", wave_number: 1 });

    expect(res.status).toBe(502);
    expect((await res.json()).detail).toContain("Unexpected response");
  });

  it("keeps the wait hint even when the reply is not JSON", async () => {
    // A gateway can answer a 429 with its own error page. The status still says
    // "too fast", so throwing the only hint of how long to wait away leaves the
    // button guessing on the one status where the answer was sent.
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response("<html>429 Too Many Requests</html>", {
          status: 429,
          headers: { "Retry-After": "30" },
        })
      )
    );

    const res = await post({ plate_id: "P7", wave_number: 1 });

    expect(res.status).toBe(429);
    expect(res.headers.get("Retry-After")).toBe("30");
  });

  it("logs a service it could not reach, since nothing upstream saw the request", async () => {
    const error = vi.spyOn(console, "error").mockImplementation(() => {});
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("fetch failed")));

    await post({ plate_id: "P7", wave_number: 1 });

    expect(error).toHaveBeenCalled();
  });

  it("lets nothing but Retry-After cross from the service", async () => {
    // The upstream headers are the service's own -- its server banner, its
    // cookies, whatever a gateway added. Only the wait hint is for the browser.
    vi.stubGlobal(
      "fetch",
      upstreamReturns(
        429,
        { detail: "this plate is already being rendered" },
        {
          "Retry-After": "30",
          "Set-Cookie": "workflows_session=abc; Path=/",
          Server: "uvicorn",
          "X-Upstream-Host": "workflows:5100",
        }
      )
    );

    const res = await post({ plate_id: "P7", wave_number: 1 });

    expect(res.headers.get("Retry-After")).toBe("30");
    expect(res.headers.get("Set-Cookie")).toBeNull();
    expect(res.headers.get("Server")).toBeNull();
    expect(res.headers.get("X-Upstream-Host")).toBeNull();
  });
});

describe("GET", () => {
  it("reports the stored video's url", async () => {
    mockedStored.mockResolvedValue({
      status: "present",
      url: "https://x/y.mp4",
      frames: 86,
    });

    const res = await get("plate_id=P7&wave_number=1");

    expect(res.status).toBe(200);
    expect((await res.json()).download_url).toBe("https://x/y.mp4");
  });

  it("reports what the stored video holds, not just that it exists", async () => {
    // The count is recorded on gravi_plate_videos when the video is made; the
    // poll is where a caller finds out without a second round trip.
    mockedStored.mockResolvedValue({
      status: "present",
      url: "https://x/y.mp4",
      frames: 86,
    });

    const res = await get("plate_id=P7&wave_number=1");

    expect((await res.json()).frames).toBe(86);
  });

  it("still serves a video whose row is missing, with no count", async () => {
    // Storage decides whether it exists. A missing row is a broken record, not
    // an empty video -- the object plays either way.
    mockedStored.mockResolvedValue({
      status: "present",
      url: "https://x/y.mp4",
      frames: null,
    });

    const res = await get("plate_id=P7&wave_number=1");
    const body = await res.json();

    expect(body.download_url).toBe("https://x/y.mp4");
    expect(body.frames).toBeNull();
  });

  it("reports no url when nothing is stored", async () => {
    mockedStored.mockResolvedValue({ status: "absent" });

    expect((await (await get("plate_id=P7&wave_number=1")).json()).download_url).toBeNull();
  });

  it("answers 503 when storage cannot say, rather than reporting no video", async () => {
    // Reported as an absence, the button would offer to render a plate that
    // already has a video, and the poll would never settle.
    mockedStored.mockResolvedValue({ status: "unknown", reason: "gateway timeout" });

    expect((await get("plate_id=P7&wave_number=1")).status).toBe(503);
  });

  it("refuses a bad plate id without touching storage", async () => {
    const res = await get("plate_id=../secrets&wave_number=1");

    expect(res.status).toBe(400);
    expect(mockedStored).not.toHaveBeenCalled();
  });

  it("asks storage for the wave the caller named", async () => {
    // Nothing else pins this. Ignored, every plate that has a wave would be
    // answered from the wrong wave's video, or from none.
    mockedStored.mockResolvedValue({ status: "absent" });

    await get("plate_id=P7&wave_number=3");

    expect(mockedStored).toHaveBeenCalledWith(12, "P7", 3);
  });

  it("treats wave zero as a wave, not as no wave", async () => {
    mockedStored.mockResolvedValue({ status: "absent" });

    await get("plate_id=P7&wave_number=0");

    expect(mockedStored).toHaveBeenCalledWith(12, "P7", 0);
  });

  it.each([["wave_number=-1"], ["wave_number=1.5"], ["wave_number=abc"]])(
    "refuses the poll's %s without touching storage",
    async (wave) => {
      const res = await get(`plate_id=P7&${wave}`);

      expect(res.status).toBe(400);
      expect(mockedStored).not.toHaveBeenCalled();
    }
  );

  it.each([["0"], ["-3"], ["12abc"], ["1e3"]])(
    "refuses the poll's experiment id %s without touching storage",
    async (experimentId) => {
      const res = await get("plate_id=P7&wave_number=1", experimentId);

      expect(res.status).toBe(400);
      expect((await res.json()).detail).toContain("experimentId");
      expect(mockedStored).not.toHaveBeenCalled();
    }
  );

  it("reads a missing wave as no wave", async () => {
    mockedStored.mockResolvedValue({ status: "absent" });

    await get("plate_id=P7");
    expect(mockedStored).toHaveBeenCalledWith(12, "P7", null);
  });

  it("logs why a poll could not read storage, so the failure is not silent", async () => {
    // The caller gets one sentence by design. Without this, "why does this plate
    // never settle?" has no answer anywhere.
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    mockedStored.mockResolvedValue({ status: "unknown", reason: "gateway timeout" });

    await get("plate_id=P7&wave_number=1");

    expect(warn).toHaveBeenCalledWith(expect.stringContaining("gateway timeout"));
  });

  it("reads an empty wave parameter as no wave, not as wave zero", async () => {
    // `?wave_number=` is what a client writing `${wave ?? ""}` sends for a
    // wave-less plate. Read as zero it looks under a key nothing was stored at.
    mockedStored.mockResolvedValue({ status: "absent" });

    await get("plate_id=P7&wave_number=");

    expect(mockedStored).toHaveBeenCalledWith(12, "P7", null);
  });

  it("reads a serialised null as no wave, rather than refusing it", async () => {
    // A client sending `${String(wave)}` sends the text "null". Refused, the
    // poll never settles for a plate that has no wave.
    mockedStored.mockResolvedValue({ status: "absent" });

    const res = await get("plate_id=P7&wave_number=null");

    expect(res.status).toBe(200);
    expect(mockedStored).toHaveBeenCalledWith(12, "P7", null);
  });

  it("answers a lookup that throws the way it answers one that fails", async () => {
    // Not an absence. Left to escape, the caller gets a bare 500 that no rule
    // about the poll's answers applies to.
    vi.spyOn(console, "error").mockImplementation(() => {});
    mockedStored.mockRejectedValue(new Error("supabase client not configured"));

    const res = await get("plate_id=P7&wave_number=1");

    expect(res.status).toBe(503);
    expect(res.headers.get("Cache-Control")).toBe("no-store");
  });

  it("answers with the download link and the count, and nothing else", async () => {
    mockedStored.mockResolvedValue({
      status: "present",
      url: "https://x/y.mp4",
      frames: 86,
    });

    const body = await (await get("plate_id=P7&wave_number=1")).json();

    expect(Object.keys(body).sort()).toEqual(["download_url", "frames"]);
  });

  it("asks the service for this plate's progress, and passes it on", async () => {
    // Nothing else pins the upstream request: without this, renaming the query
    // parameter or dropping the token ships the feature dead with CI green.
    mockedStored.mockResolvedValue({ status: "absent" });
    const fetchMock = vi
      .fn()
      .mockResolvedValue(
        new Response(JSON.stringify({ stage: "downloading", done: 47, total: 86 }), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        })
      );
    vi.stubGlobal("fetch", fetchMock);

    const body = await (await get("plate_id=P7&wave_number=1&progress=1")).json();

    const [url, init] = fetchMock.mock.calls[0];
    expect(String(url)).toContain("/gravi/experiments/12/plate-video/progress");
    expect(String(url)).toContain("plate_id=P7");
    expect(String(url)).toContain("wave_number=1");
    expect(init.headers.Authorization).toBe("Bearer token");
    expect(body.progress).toEqual({ stage: "downloading", done: 47, total: 86 });
  });

  it("omits the wave for a wave-less plate rather than sending null", async () => {
    mockedStored.mockResolvedValue({ status: "absent" });
    const fetchMock = vi.fn().mockResolvedValue(new Response("{}", { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);

    await get("plate_id=P7&progress=1");

    expect(String(fetchMock.mock.calls[0][0])).not.toContain("wave_number");
  });

  it("keeps a refused progress answer out of the poll's reply", async () => {
    // The status decides, not the shape: a refusal whose body happens to be
    // well-formed progress must not reach the page.
    for (const bad of [
      new Response("upstream exploded", { status: 500 }),
      new Response(JSON.stringify({ stage: "downloading", done: 3, total: 9 }), {
        status: 503,
      }),
      new Response(JSON.stringify({ stage: "downloading", done: 3 }), { status: 200 }),
    ]) {
      mockedStored.mockResolvedValue({ status: "absent" });
      vi.stubGlobal("fetch", vi.fn().mockResolvedValue(bad));

      const res = await get("plate_id=P7&wave_number=1&progress=1");
      const body = await res.json();

      expect(res.status).toBe(200);
      expect(body.download_url).toBeNull();
      expect(body.progress).toBeNull();
    }
  });

  it("passes on only the three fields the page reads", async () => {
    mockedStored.mockResolvedValue({ status: "absent" });
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(
          JSON.stringify({ stage: "downloading", done: 3, total: 9, secret: "x" }),
          { status: 200 }
        )
      )
    );

    const body = await (await get("plate_id=P7&wave_number=1&progress=1")).json();

    expect(Object.keys(body.progress).sort()).toEqual(["done", "stage", "total"]);
  });

  it("still answers the poll when the session cannot be read", async () => {
    // The poll's job is to say whether the video is there. A session read that
    // throws must not take the whole answer down with it.
    mockedStored.mockResolvedValue({ status: "absent" });
    mockedGetSession.mockRejectedValue(new Error("cookie jar exploded"));

    const res = await get("plate_id=P7&wave_number=1&progress=1");

    expect(res.status).toBe(200);
    expect((await res.json()).progress).toBeNull();
  });

  it("does not ask the service for progress unless the caller wants it", async () => {
    // The page's first look does not read progress, and the call costs an
    // authenticated round trip to the service.
    mockedStored.mockResolvedValue({ status: "absent" });
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);

    const body = await (await get("plate_id=P7&wave_number=1")).json();

    expect(fetchMock).not.toHaveBeenCalled();
    expect(body).not.toHaveProperty("progress");
  });

  it("does not fail the poll when progress cannot be read", async () => {
    // Progress is decoration. A poll that fails because of it would stop the
    // button ever learning the video is ready.
    mockedStored.mockResolvedValue({ status: "absent" });
    vi.stubGlobal(
      "fetch",
      vi.fn().mockRejectedValue(new TypeError("fetch failed"))
    );

    const res = await get("plate_id=P7&wave_number=1&progress=1");
    const body = await res.json();

    expect(res.status).toBe(200);
    expect(body.progress).toBeNull();
  });

  it("forbids storing the answer that carries the download link", async () => {
    // The link is signed for one reader and expires. A stored copy is either
    // served to someone it was not made for, or served after it stopped working.
    mockedStored.mockResolvedValue({
      status: "present",
      url: "https://x/y.mp4?token=t",
      frames: 86,
    });

    const res = await get("plate_id=P7&wave_number=1");

    expect(res.headers.get("Cache-Control")).toBe("no-store");
  });

  it("forbids storing a not-yet answer, which a later poll is meant to contradict", async () => {
    // Polling only works because the answer changes. A stored "no video" leaves
    // the button waiting on one that has already been made.
    mockedStored.mockResolvedValue({ status: "absent" });

    const res = await get("plate_id=P7&wave_number=1");

    expect(res.headers.get("Cache-Control")).toBe("no-store");
  });

  it("forbids storing any of its answers, so none of them stick", async () => {
    mockedStored.mockResolvedValue({
      status: "unknown",
      reason: "gateway timeout",
    });

    const answers = [
      await get("plate_id=P7&wave_number=1"), // 503, storage could not say
      await get("plate_id=../secrets"), // 400, bad plate id
      await get("plate_id=P7&wave_number=-1"), // 400, bad wave
      await get("plate_id=P7", "0"), // 400, bad experiment id
    ];

    for (const res of answers) {
      expect(res.headers.get("Cache-Control")).toBe("no-store");
    }
  });
});
