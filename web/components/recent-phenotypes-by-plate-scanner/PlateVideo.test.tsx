// @vitest-environment jsdom
/**
 * The three states a plate can be in, and the 504 recovery.
 *
 * A plate keeps gaining captures, so unlike a cylinder scan a stored video is
 * usually not wrong — just short. "Stale" is the state that exists for that,
 * and the count it shows has to be the encoder's count: the page joins images
 * loosely, the encoder joins them with `!inner`, so a scan without an image
 * must not be offered as a frame.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";

const createSignedUrl = vi.fn();
vi.mock("@/lib/supabase/client", () => ({
  createClientSupabaseClient: () => ({
    storage: { from: () => ({ createSignedUrl }) },
  }),
}));

import { PlateVideo } from "./PlateVideo";

const SIGNED = "https://signed.test/graviscan-videos/12/wave-1/P7.mp4?token=a";
const POLLED = "https://signed.test/graviscan-videos/12/wave-1/P7.mp4?token=b";

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

/** Routes fetch by method: HEAD is the stored-object probe, the rest is the API. */
function routeFetch(api: (url: string, init?: RequestInit) => Response) {
  const calls: { url: string; init?: RequestInit }[] = [];
  const fetchMock = vi.fn(async (input: unknown, init?: RequestInit) => {
    const url = String(input);
    if (init?.method === "HEAD") return new Response(null, { status: 200 });
    calls.push({ url, init });
    return api(url, init);
  });
  vi.stubGlobal("fetch", fetchMock);
  return calls;
}

function renderPlate(props: Partial<Parameters<typeof PlateVideo>[0]> = {}) {
  return render(
    <PlateVideo
      experimentId={12}
      plateId="P7"
      waveNumber={1}
      objectPath={null}
      storedFrames={null}
      availableFrames={40}
      {...props}
    />,
  );
}

const STORED = { objectPath: "12/wave-1/P7.mp4" };

beforeEach(() => {
  vi.clearAllMocks();
  createSignedUrl.mockResolvedValue({ data: { signedUrl: SIGNED }, error: null });
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

describe("which state the plate is in", () => {
  it("offers Generate when nothing is stored", async () => {
    routeFetch(() => json({}));
    await act(async () => {
      renderPlate();
    });
    expect(screen.getByRole("button").textContent).toContain("Generate");
    expect(screen.getByText(/No time-lapse video/)).toBeTruthy();
  });

  it("offers nothing when no capture has an image", async () => {
    routeFetch(() => json({}));
    await act(async () => {
      renderPlate({ availableFrames: 0 });
    });
    expect(screen.queryByRole("button")).toBeNull();
    expect(screen.getByText(/No captures with an image/)).toBeTruthy();
  });

  it("offers nothing when the stored video covers every frame", async () => {
    routeFetch(() => json({}));
    await act(async () => {
      renderPlate({ ...STORED, storedFrames: 40, availableFrames: 40 });
    });
    expect(screen.queryByRole("button")).toBeNull();
  });

  it("offers Update, with the count, when frames have arrived since", async () => {
    routeFetch(() => json({}));
    await act(async () => {
      renderPlate({ ...STORED, storedFrames: 16, availableFrames: 40 });
    });
    expect(screen.getByRole("button").textContent).toContain("Update");
    expect(screen.getByText("24 new frames since this was made.")).toBeTruthy();
  });

  it("says frame, not frames, for one", async () => {
    routeFetch(() => json({}));
    await act(async () => {
      renderPlate({ ...STORED, storedFrames: 39, availableFrames: 40 });
    });
    expect(screen.getByText("1 new frame since this was made.")).toBeTruthy();
  });

  it("offers Update, without a count, when the stored frame count is unknown", async () => {
    routeFetch(() => json({}));
    await act(async () => {
      renderPlate({ ...STORED, storedFrames: null, availableFrames: 40 });
    });
    // A null count is why the service re-renders rather than keeps; the button
    // has to offer that, but it cannot honestly say how many are new.
    expect(screen.getByRole("button").textContent).toContain("Update");
    expect(screen.queryByText(/new frame/)).toBeNull();
  });

  it("does not offer a video whose object is gone", async () => {
    createSignedUrl.mockResolvedValue({ data: { signedUrl: SIGNED }, error: null });
    const fetchMock = vi.fn(async (_input: unknown, init?: RequestInit) =>
      init?.method === "HEAD" ? new Response(null, { status: 404 }) : json({}),
    );
    vi.stubGlobal("fetch", fetchMock);
    await act(async () => {
      renderPlate({ ...STORED, storedFrames: 40, availableFrames: 40 });
    });
    expect(screen.queryByRole("video")).toBeNull();
    expect(screen.getByText(/No time-lapse video/)).toBeTruthy();
  });
});

describe("generating", () => {
  it("sends the plate and wave the page is showing", async () => {
    const calls = routeFetch(() => json({ object_path: "12/wave-1/P7.mp4" }));
    await act(async () => {
      renderPlate();
    });
    await act(async () => {
      fireEvent.click(screen.getByRole("button"));
    });
    expect(calls).toHaveLength(1);
    expect(calls[0].url).toBe("/api/gravi/experiments/12/plate-video");
    expect(JSON.parse(String(calls[0].init?.body))).toEqual({
      plate_id: "P7",
      wave_number: 1,
    });
  });

  it("sends a null wave as null", async () => {
    const calls = routeFetch(() => json({ object_path: "12/P7.mp4" }));
    await act(async () => {
      renderPlate({ waveNumber: null });
    });
    await act(async () => {
      fireEvent.click(screen.getByRole("button"));
    });
    expect(JSON.parse(String(calls[0].init?.body)).wave_number).toBeNull();
  });

  it("shows the video and stops offering Update once it lands", async () => {
    routeFetch(() => json({ object_path: "12/wave-1/P7.mp4" }));
    await act(async () => {
      renderPlate({ ...STORED, storedFrames: 16, availableFrames: 40 });
    });
    expect(screen.getByRole("button").textContent).toContain("Update");
    await act(async () => {
      fireEvent.click(screen.getByRole("button"));
    });
    expect(screen.queryByRole("button")).toBeNull();
    expect(screen.queryByText(/new frame/)).toBeNull();
  });

  it("does not fire a second request while one is in flight", async () => {
    let release: (r: Response) => void = () => {};
    const pending = new Promise<Response>((resolve) => {
      release = resolve;
    });
    const calls: string[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: unknown, init?: RequestInit) => {
        if (init?.method === "HEAD") return new Response(null, { status: 200 });
        calls.push(String(input));
        return pending;
      }),
    );
    await act(async () => {
      renderPlate();
    });
    await act(async () => {
      fireEvent.click(screen.getByRole("button"));
    });
    expect(screen.getByRole("button").hasAttribute("disabled")).toBe(true);
    fireEvent.click(screen.getByRole("button"));
    await act(async () => {
      release(json({ object_path: "12/wave-1/P7.mp4" }));
    });
    expect(calls).toHaveLength(1);
  });

  it("reports one sentence when the request is refused", async () => {
    routeFetch(() => json({ detail: "this plate has no captures with an image" }, 404));
    await act(async () => {
      renderPlate();
    });
    await act(async () => {
      fireEvent.click(screen.getByRole("button"));
    });
    expect(screen.getByText(/Could not generate the video/)).toBeTruthy();
  });

  it("reports one sentence when the response carries no object path", async () => {
    // A 200 whose body is not what we expect is a failure, not a success with
    // nothing to show — treating it as success would blank the player.
    routeFetch(() => json({ action: "render" }));
    await act(async () => {
      renderPlate({ ...STORED, storedFrames: 16 });
    });
    await act(async () => {
      fireEvent.click(screen.getByRole("button"));
    });
    expect(screen.getByText(/Could not generate the video/)).toBeTruthy();
  });

  it("reports one sentence when the network fails outright", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (_i: unknown, init?: RequestInit) => {
        if (init?.method === "HEAD") return new Response(null, { status: 200 });
        throw new TypeError("network down");
      }),
    );
    await act(async () => {
      renderPlate();
    });
    await act(async () => {
      fireEvent.click(screen.getByRole("button"));
    });
    expect(screen.getByText(/Could not generate the video/)).toBeTruthy();
  });
});

describe("a 504, and the poll that follows", () => {
  it("polls until the video appears, then shows it", async () => {
    vi.useFakeTimers();
    let polls = 0;
    const calls = routeFetch((url, init) => {
      if (init?.method === "POST") return json({ detail: null }, 504);
      polls += 1;
      return json({ download_url: polls >= 2 ? POLLED : null });
    });
    await act(async () => {
      renderPlate();
    });
    await act(async () => {
      fireEvent.click(screen.getByRole("button"));
    });
    expect(screen.getByText(/Encoding/)).toBeTruthy();

    await act(async () => {
      await vi.advanceTimersByTimeAsync(10_000);
    });
    expect(polls).toBe(1);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(10_000);
    });
    expect(polls).toBe(2);
    expect(screen.queryByText(/Encoding/)).toBeNull();

    const poll = calls.find((c) => c.url.includes("?"));
    expect(poll?.url).toBe(
      "/api/gravi/experiments/12/plate-video?plate_id=P7&wave_number=1",
    );
  });

  it("omits the wave from the poll when there is none", async () => {
    vi.useFakeTimers();
    const calls = routeFetch((_url, init) =>
      init?.method === "POST" ? json({}, 504) : json({ download_url: POLLED }),
    );
    await act(async () => {
      renderPlate({ waveNumber: null });
    });
    await act(async () => {
      fireEvent.click(screen.getByRole("button"));
    });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(10_000);
    });
    const poll = calls.find((c) => c.url.includes("?"));
    expect(poll?.url).toBe("/api/gravi/experiments/12/plate-video?plate_id=P7");
  });

  it("keeps polling through a failed poll rather than giving up", async () => {
    vi.useFakeTimers();
    let polls = 0;
    routeFetch((_url, init) => {
      if (init?.method === "POST") return json({}, 504);
      polls += 1;
      if (polls === 1) return json({ detail: "unavailable" }, 503);
      return json({ download_url: POLLED });
    });
    await act(async () => {
      renderPlate();
    });
    await act(async () => {
      fireEvent.click(screen.getByRole("button"));
    });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(20_000);
    });
    expect(polls).toBe(2);
    expect(screen.queryByText(/Encoding/)).toBeNull();
  });

  it("stops asking after the budget, without re-offering Generate", async () => {
    vi.useFakeTimers();
    routeFetch((_url, init) =>
      init?.method === "POST" ? json({}, 504) : json({ download_url: null }),
    );
    await act(async () => {
      renderPlate();
    });
    await act(async () => {
      fireEvent.click(screen.getByRole("button"));
    });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(600_000);
    });
    expect(screen.getByText(/Check back in a few minutes/)).toBeTruthy();
    // Re-offering it here would start a second encode on a plate that is
    // already being encoded.
    expect(screen.queryByRole("button")).toBeNull();
  });
});
