// @vitest-environment jsdom
/**
 * The three states a plate can be in, the 504 recovery, and what a scientist
 * is told when something goes wrong.
 *
 * A plate keeps gaining captures, so unlike a cylinder scan a stored video is
 * usually not wrong — just short. "Stale" is the state that exists for that,
 * and the count it shows has to be the encoder's count: the page joins images
 * loosely, the encoder joins them with `!inner`, so a scan without an image
 * must not be offered as a frame.
 *
 * Whether a video exists is asked of the route, never worked out here. Storage
 * decides presence and the route signs the link, so a lookup that failed comes
 * back as "could not check" rather than as "no video".
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";

import { PlateVideo } from "./PlateVideo";

const URL_A = "https://signed.test/graviscan-videos/12/wave-1/P7.mp4?token=a";
const URL_B = "https://signed.test/graviscan-videos/12/wave-1/P7.mp4?token=b";

const RENDERED = {
  experiment_id: 12,
  plate_id: "P7",
  wave_number: 1,
  action: "rendered",
  reason: "no video stored; encoding 40 frames",
  object_path: "12/wave-1/P7.mp4",
  frames: 40,
  coverage: null,
};

function json(body: unknown, status = 200, headers: Record<string, string> = {}) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json", ...headers },
  });
}

/** Serves the route: GET is the poll, POST is the render. */
function serve(handler: (method: string, url: string) => Response) {
  const calls: { method: string; url: string; init?: RequestInit }[] = [];
  const fetchMock = vi.fn(async (input: unknown, init?: RequestInit) => {
    const method = init?.method ?? "GET";
    calls.push({ method, url: String(input), init });
    return handler(method, String(input));
  });
  vi.stubGlobal("fetch", fetchMock);
  return calls;
}

/** The common case: the poll answers, nothing is stored. */
const nothingStored = () => serve(() => json({ download_url: null, frames: null }));

/** The common case: the poll answers with a video holding `frames`. */
const storedWith = (frames: number | null, url = URL_A) =>
  serve(() => json({ download_url: url, frames }));

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
    />
  );
}

const STORED = { objectPath: "12/wave-1/P7.mp4" };

beforeEach(() => vi.clearAllMocks());

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

describe("which state the plate is in", () => {
  it("offers Generate when nothing is stored", async () => {
    nothingStored();
    await act(async () => {
      renderPlate();
    });
    expect(screen.getByRole("button").textContent).toContain("Generate");
    expect(screen.getByText(/No time-lapse video/)).toBeTruthy();
  });

  it("offers nothing when no capture has an image", async () => {
    nothingStored();
    await act(async () => {
      renderPlate({ availableFrames: 0 });
    });
    expect(screen.queryByRole("button")).toBeNull();
    expect(screen.getByText(/No captures with an image/)).toBeTruthy();
  });

  it("offers nothing when the stored video covers every frame", async () => {
    storedWith(40);
    await act(async () => {
      renderPlate({ ...STORED, storedFrames: 40, availableFrames: 40 });
    });
    expect(screen.queryByRole("button")).toBeNull();
  });

  it("offers Update, with the count, when frames have arrived since", async () => {
    storedWith(16);
    await act(async () => {
      renderPlate({ ...STORED, storedFrames: 16, availableFrames: 40 });
    });
    expect(screen.getByRole("button").textContent).toContain("Update");
    expect(screen.getByText("24 new frames since this was made.")).toBeTruthy();
  });

  it("says frame, not frames, for one", async () => {
    storedWith(39);
    await act(async () => {
      renderPlate({ ...STORED, storedFrames: 39, availableFrames: 40 });
    });
    expect(screen.getByText("1 new frame since this was made.")).toBeTruthy();
  });

  it("offers Update, without a count, when the stored frame count is unknown", async () => {
    // A null count is why the service re-renders rather than keeps; the button
    // has to offer that, but it cannot honestly say how many are new.
    storedWith(null);
    await act(async () => {
      renderPlate({ ...STORED, storedFrames: null, availableFrames: 40 });
    });
    expect(screen.getByRole("button").textContent).toContain("Update");
    expect(screen.queryByText(/new frame/)).toBeNull();
  });

  it("takes the frame count from the answer, not from the page's row", async () => {
    // The row was read when the page rendered; the answer is current. A stale
    // row would have the button promise new frames a fresh video already holds.
    storedWith(40);
    await act(async () => {
      renderPlate({ ...STORED, storedFrames: 16, availableFrames: 40 });
    });
    expect(screen.queryByRole("button")).toBeNull();
    expect(screen.queryByText(/new frame/)).toBeNull();
  });

  it("does not offer a video whose object is gone", async () => {
    // The row can outlive the object. Storage decides, so the route answers no
    // video even though the page rendered with a path.
    nothingStored();
    await act(async () => {
      renderPlate({ ...STORED, storedFrames: 40, availableFrames: 40 });
    });
    expect(document.querySelector("video")).toBeNull();
    expect(screen.getByText(/No time-lapse video/)).toBeTruthy();
  });

  it("shows the whole frame, so the burnt-in timestamp is not cropped", async () => {
    // A plate renders at 1440x2068 into a 5:7 box. Cropped to fill, the top and
    // bottom go — including the label band this feature exists to burn in.
    storedWith(40);
    await act(async () => {
      renderPlate({ ...STORED, storedFrames: 40, availableFrames: 40 });
    });
    expect(document.querySelector("video")?.className).toContain("object-contain");
  });

  it("plays the video the route signed", async () => {
    storedWith(40);
    await act(async () => {
      renderPlate({ ...STORED, storedFrames: 40, availableFrames: 40 });
    });
    expect(document.querySelector("source")?.getAttribute("src")).toBe(URL_A);
  });
});

describe("when the check itself fails", () => {
  it("says so, rather than reporting no video", async () => {
    // The failure this whole design exists to prevent: read as an absence, the
    // page offers to re-render a plate that already has a video.
    serve(() => json({ detail: "Could not check" }, 503));
    await act(async () => {
      renderPlate({ ...STORED, storedFrames: 40, availableFrames: 40 });
    });
    expect(screen.getByText(/Could not check whether this plate has a video/)).toBeTruthy();
  });

  it("does not offer Generate over a video it could not check for", async () => {
    serve(() => json({ detail: "Could not check" }, 503));
    await act(async () => {
      renderPlate();
    });
    expect(screen.queryByRole("button")).toBeNull();
  });

  it("treats a network failure the same way", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("fetch failed")));
    await act(async () => {
      renderPlate();
    });
    expect(screen.queryByRole("button")).toBeNull();
    expect(screen.getByText(/Could not check/)).toBeTruthy();
  });
});

describe("generating", () => {
  it("sends the plate and wave the page is showing", async () => {
    const calls = serve((method) =>
      method === "POST" ? json(RENDERED) : json({ download_url: null, frames: null })
    );
    await act(async () => {
      renderPlate();
    });
    await act(async () => {
      fireEvent.click(screen.getByRole("button"));
    });

    const post = calls.find((c) => c.method === "POST");
    expect(post?.url).toBe("/api/gravi/experiments/12/plate-video");
    expect(JSON.parse(String(post?.init?.body))).toEqual({
      plate_id: "P7",
      wave_number: 1,
    });
  });

  it("omits nothing for a plate with no wave — it sends null", async () => {
    const calls = serve((method) =>
      method === "POST" ? json(RENDERED) : json({ download_url: null, frames: null })
    );
    await act(async () => {
      renderPlate({ waveNumber: null });
    });
    await act(async () => {
      fireEvent.click(screen.getByRole("button"));
    });

    const post = calls.find((c) => c.method === "POST");
    expect(JSON.parse(String(post?.init?.body)).wave_number).toBeNull();
  });

  it("asks the route for the link once the render lands", async () => {
    // A 200 carries no playable URL, only the object path, so the poll is the
    // success path as well as the timeout path.
    let stored = false;
    const calls = serve((method) => {
      if (method === "POST") {
        stored = true;
        return json(RENDERED);
      }
      return json({ download_url: stored ? URL_B : null, frames: stored ? 40 : null });
    });
    await act(async () => {
      renderPlate();
    });
    await act(async () => {
      fireEvent.click(screen.getByRole("button"));
    });

    expect(document.querySelector("source")?.getAttribute("src")).toBe(URL_B);
    expect(screen.queryByRole("button")).toBeNull();
    expect(calls.filter((c) => c.method === "GET")).toHaveLength(2);
  });

  it("keeps offering Update when the render covered fewer frames than exist", async () => {
    // More captures can land while the encode runs. Assuming the new video
    // covers everything available would hide the ones it missed.
    let encoded = false;
    serve((method) => {
      if (method === "POST") {
        encoded = true;
        return json({ ...RENDERED, frames: 30 });
      }
      return json({
        download_url: encoded ? URL_B : null,
        frames: encoded ? 30 : null,
      });
    });
    await act(async () => {
      renderPlate({ availableFrames: 40 });
    });
    await act(async () => {
      fireEvent.click(screen.getByRole("button"));
    });

    expect(screen.getByRole("button").textContent).toContain("Update");
    expect(screen.getByText("10 new frames since this was made.")).toBeTruthy();
  });

  it("does not fire a second request while one is in flight", async () => {
    let release: (r: Response) => void = () => {};
    const calls = serve((method) => {
      if (method === "GET") return json({ download_url: null, frames: null });
      return new Promise<Response>((r) => (release = r)) as unknown as Response;
    });
    await act(async () => {
      renderPlate();
    });
    const button = screen.getByRole("button");
    await act(async () => {
      fireEvent.click(button);
    });
    await act(async () => {
      fireEvent.click(button);
    });

    expect(calls.filter((c) => c.method === "POST")).toHaveLength(1);
    expect(button.hasAttribute("disabled")).toBe(true);
    await act(async () => {
      release(json(RENDERED));
    });
  });
});

describe("what a refusal tells the scientist", () => {
  it("shows the service's own sentence, not a generic one", async () => {
    // The service knows things this component cannot: whether the captures are
    // still uploading, whether another plate is encoding, whether to sign in.
    serve((method) =>
      method === "POST"
        ? json({ detail: "none of this plate's images have finished uploading yet" }, 404)
        : json({ download_url: null, frames: null })
    );
    await act(async () => {
      renderPlate();
    });
    await act(async () => {
      fireEvent.click(screen.getByRole("button"));
    });

    expect(screen.getByText(/images have finished uploading/)).toBeTruthy();
  });

  it("passes on the wait when the service says how long", async () => {
    serve((method) =>
      method === "POST"
        ? json(
            { detail: "another plate video is already encoding" },
            429,
            { "Retry-After": "30" }
          )
        : json({ download_url: null, frames: null })
    );
    await act(async () => {
      renderPlate();
    });
    await act(async () => {
      fireEvent.click(screen.getByRole("button"));
    });

    expect(screen.getByText(/already encoding/)).toBeTruthy();
    expect(screen.getByText(/Try again in 30 seconds/)).toBeTruthy();
  });

  it("shows the detail as text, never as markup", async () => {
    serve((method) =>
      method === "POST"
        ? json({ detail: "<img src=x onerror=alert(1)> failed" }, 422)
        : json({ download_url: null, frames: null })
    );
    await act(async () => {
      renderPlate();
    });
    await act(async () => {
      fireEvent.click(screen.getByRole("button"));
    });

    expect(document.querySelector("img")).toBeNull();
    expect(screen.getByText(/onerror=alert\(1\)/)).toBeTruthy();
  });

  it("falls back to its own sentence when the service sends nothing readable", async () => {
    serve((method) =>
      method === "POST"
        ? new Response("<html>502</html>", { status: 502 })
        : json({ download_url: null, frames: null })
    );
    await act(async () => {
      renderPlate();
    });
    await act(async () => {
      fireEvent.click(screen.getByRole("button"));
    });

    expect(screen.getByText(/Could not generate the video/)).toBeTruthy();
    expect(screen.queryByText(/html/)).toBeNull();
  });

  it("reports a response that carries no object path", async () => {
    serve((method) =>
      method === "POST" ? json({ ok: true }) : json({ download_url: null, frames: null })
    );
    await act(async () => {
      renderPlate();
    });
    await act(async () => {
      fireEvent.click(screen.getByRole("button"));
    });

    expect(screen.getByText(/Could not generate the video/)).toBeTruthy();
  });

  it("reports a network failure outright", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(json({ download_url: null, frames: null }))
      .mockRejectedValue(new TypeError("fetch failed"));
    vi.stubGlobal("fetch", fetchMock);
    await act(async () => {
      renderPlate();
    });
    await act(async () => {
      fireEvent.click(screen.getByRole("button"));
    });

    expect(screen.getByText(/Could not generate the video/)).toBeTruthy();
  });
});

describe("what a scientist is told while it renders", () => {
  const rendering = (progress: unknown) =>
    serve((method) =>
      method === "POST"
        ? json({}, 504)
        : json({ download_url: null, frames: null, progress })
    );

  async function clickAndPoll(ticks = 1) {
    await act(async () => {
      renderPlate();
    });
    await act(async () => {
      fireEvent.click(screen.getByRole("button"));
    });
    for (let i = 0; i < ticks; i++) {
      await act(async () => {
        await vi.advanceTimersByTimeAsync(10_000);
      });
    }
  }

  it("counts the frames as they download", async () => {
    // Downloading is ~96% of a render, so this is the number they watch.
    vi.useFakeTimers();
    rendering({ stage: "downloading", done: 46, total: 86 });

    await clickAndPoll();

    expect(screen.getByText("Downloading frame 47 of 86")).toBeTruthy();
  });

  it("says the video is being made once the frames are in", async () => {
    vi.useFakeTimers();
    rendering({ stage: "encoding", done: 86, total: 86 });

    await clickAndPoll();

    expect(screen.getByText(/please stay on this page/)).toBeTruthy();
  });

  it("falls back to the plain wait when the service reports nothing", async () => {
    // An older service, a restarted one, or the seconds before the first frame.
    vi.useFakeTimers();
    rendering(undefined);

    await clickAndPoll();

    expect(
      screen.getByText(/Encoding — this can take a few minutes/)
    ).toBeTruthy();
  });

  it("never lets the count go backwards", async () => {
    // Two polls can land out of order; a count that jumps back reads as broken.
    vi.useFakeTimers();
    let asked = false;
    let n = 0;
    const counts = [60, 20];
    serve((method) => {
      if (method === "POST") {
        asked = true;
        return json({}, 504);
      }
      if (!asked) return json({ download_url: null, frames: null });
      return json({
        download_url: null,
        frames: null,
        progress: { stage: "downloading", done: counts[n++] ?? 20, total: 86 },
      });
    });

    await clickAndPoll(2);

    expect(screen.getByText("Downloading frame 61 of 86")).toBeTruthy();
  });
});

describe("a 504, and the poll that follows", () => {
  it("polls until the video appears, then shows it", async () => {
    vi.useFakeTimers();
    let polls = 0;
    const calls = serve((method) => {
      if (method === "POST") return json({ detail: "Still encoding" }, 504);
      polls += 1;
      return json({ download_url: polls >= 3 ? URL_B : null, frames: polls >= 3 ? 40 : null });
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
    expect(screen.getByText(/Encoding/)).toBeTruthy();

    await act(async () => {
      await vi.advanceTimersByTimeAsync(10_000);
    });
    expect(screen.queryByText(/Encoding/)).toBeNull();
    expect(document.querySelector("source")?.getAttribute("src")).toBe(URL_B);

    const poll = calls.find((c) => c.method === "GET");
    expect(poll?.url).toBe(
      "/api/gravi/experiments/12/plate-video?plate_id=P7&wave_number=1"
    );
  });

  it("omits the wave from the poll when there is none", async () => {
    vi.useFakeTimers();
    let encoded = false;
    const calls = serve((method) => {
      if (method === "POST") {
        encoded = true;
        return json({}, 504);
      }
      return json({
        download_url: encoded ? URL_B : null,
        frames: encoded ? 40 : null,
      });
    });
    await act(async () => {
      renderPlate({ waveNumber: null });
    });
    await act(async () => {
      fireEvent.click(screen.getByRole("button"));
    });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(10_000);
    });

    const poll = calls.find((c) => c.method === "GET");
    expect(poll?.url).toBe("/api/gravi/experiments/12/plate-video?plate_id=P7");
  });

  it("keeps asking through a failed poll rather than giving up", async () => {
    vi.useFakeTimers();
    let polls = 0;
    serve((method) => {
      if (method === "POST") return json({}, 504);
      polls += 1;
      if (polls === 2) return json({ detail: "Could not check" }, 503);
      return json({ download_url: polls >= 3 ? URL_B : null, frames: polls >= 3 ? 40 : null });
    });
    await act(async () => {
      renderPlate();
    });
    await act(async () => {
      fireEvent.click(screen.getByRole("button"));
    });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(30_000);
    });

    expect(document.querySelector("source")?.getAttribute("src")).toBe(URL_B);
  });

  it("stops asking after the budget, without re-offering Generate", async () => {
    // The encode it was waiting on is still running upstream; a second one
    // would race it.
    vi.useFakeTimers();
    serve((method) =>
      method === "POST" ? json({}, 504) : json({ download_url: null, frames: null })
    );
    await act(async () => {
      renderPlate();
    });
    await act(async () => {
      fireEvent.click(screen.getByRole("button"));
    });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(610_000);
    });

    expect(screen.getByText(/Still encoding/)).toBeTruthy();
    expect(screen.queryByRole("button")).toBeNull();
  });
});
