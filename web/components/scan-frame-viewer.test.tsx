// @vitest-environment jsdom
/**
 * The frame viewer's failure paths, which the pure helpers can't reach.
 *
 * A frame can be unavailable two ways: its URL fails to sign, or it signs and
 * then the image won't load (the object is gone, or the hour-long URL lapsed
 * mid-session). Both must say so — a broken-image glyph reads as a dark
 * capture, which on a phenotyping scan is a different conclusion entirely.
 */

import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

import ScanFrameViewer from "./scan-frame-viewer";

// Signs every path handed to it, unless the path is listed as unsignable.
const unsignable = new Set<string>();

vi.mock("@/lib/supabase/client", () => ({
  createClientSupabaseClient: () => ({
    storage: {
      from: () => ({
        createSignedUrls: async (paths: string[]) => ({
          data: paths.map((path) => ({
            path,
            signedUrl: unsignable.has(path) ? "" : `https://signed.test/${path}`,
          })),
        }),
      }),
    },
  }),
}));

function scanWith(frames: { n: number | null; path: string }[]) {
  return {
    id: 1,
    cyl_images: frames.map((f, i) => ({
      id: i + 1,
      frame_number: f.n,
      object_path: f.path,
      scan_id: 1,
      date_scanned: null,
      status: null,
      uploaded_at: null,
    })),
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
  } as any;
}

const THREE_FRAMES = scanWith([
  { n: 1, path: "f1.png" },
  { n: 2, path: "f2.png" },
  { n: 3, path: "f3.png" },
]);

afterEach(() => {
  cleanup();
  unsignable.clear();
});

describe("ScanFrameViewer", () => {
  it("shows the first frame of the rotation", async () => {
    const { container } = render(<ScanFrameViewer scan={THREE_FRAMES} />);

    await waitFor(() =>
      expect(container.querySelector("img")?.getAttribute("src")).toBe(
        "https://signed.test/f1.png"
      )
    );
    expect(screen.getByText("Frame 1")).toBeTruthy();
  });

  it("says so when a frame's image fails to load", async () => {
    const { container } = render(<ScanFrameViewer scan={THREE_FRAMES} />);
    const img = await waitFor(() => {
      const el = container.querySelector("img");
      if (!el) throw new Error("no image yet");
      return el;
    });

    // The URL signed; the object is gone, or the signature lapsed.
    fireEvent.error(img);

    expect(screen.getByText("Frame 1 could not be loaded.")).toBeTruthy();
    expect(container.querySelector("img")).toBeNull();
  });

  it("keeps the pager alive so the other frames stay reachable", async () => {
    const { container } = render(<ScanFrameViewer scan={THREE_FRAMES} />);
    const img = await waitFor(() => {
      const el = container.querySelector("img");
      if (!el) throw new Error("no image yet");
      return el;
    });
    fireEvent.error(img);

    fireEvent.click(screen.getByLabelText("Next frame"));

    await waitFor(() =>
      expect(container.querySelector("img")?.getAttribute("src")).toBe(
        "https://signed.test/f2.png"
      )
    );
    expect(screen.getByText("Frame 2")).toBeTruthy();
  });

  it("reports a frame that could not be signed", async () => {
    unsignable.add("f1.png");

    render(<ScanFrameViewer scan={THREE_FRAMES} />);

    await screen.findByText("Frame 1 could not be loaded.");
  });

  it("says nothing about skipped frame numbers", async () => {
    // Frames 1,2,4 all render. A count of absent frame *numbers* is a second figure
    // against a different denominator, and it tells a reader nothing they can act on.
    render(
      <ScanFrameViewer
        scan={scanWith([
          { n: 1, path: "f1.png" },
          { n: 2, path: "f2.png" },
          { n: 4, path: "f4.png" },
        ])}
      />
    );

    await screen.findByText("Frame 1");
    expect(screen.queryByText(/missing from this rotation/)).toBeNull();
    expect(screen.queryByText(/not available/)).toBeNull();
  });
});

/**
 * Paging — the feature this component is named after. The mislabel risk lives in
 * the wiring (frameIndex -> currentPath -> frameUrls.get -> <img src> -> label),
 * which the pure helpers cannot reach.
 */
describe("paging", () => {
  const GAPPED = scanWith([
    { n: 1, path: "f1.png" },
    { n: 2, path: "f2.png" },
    { n: 4, path: "f4.png" },
  ]);

  async function pagedViewer() {
    render(<ScanFrameViewer scan={GAPPED} />);
    await waitFor(() => expect(screen.getByText("Frame 1")).toBeTruthy());
  }

  it("shows the frame's own number, not its position", async () => {
    await pagedViewer();

    fireEvent.click(screen.getByRole("button", { name: /next/i }));
    fireEvent.click(screen.getByRole("button", { name: /next/i }));

    // Third in the list, but frame 4 — the whole reason positions are not labels.
    expect(screen.getByText("Frame 4")).toBeTruthy();
    const img = document.querySelector("img") as HTMLImageElement;
    expect(img.src).toContain("f4.png");
  });

  it("steps back to the frame it came from", async () => {
    await pagedViewer();

    fireEvent.click(screen.getByRole("button", { name: /next/i }));
    expect(screen.getByText("Frame 2")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: /previous/i }));

    expect(screen.getByText("Frame 1")).toBeTruthy();
    const img = document.querySelector("img") as HTMLImageElement;
    expect(img.src).toContain("f1.png");
  });

  it("cannot page past either end", async () => {
    await pagedViewer();

    const prev = screen.getByRole("button", { name: /previous/i });
    expect(prev.hasAttribute("disabled")).toBe(true);

    fireEvent.click(screen.getByRole("button", { name: /next/i }));
    fireEvent.click(screen.getByRole("button", { name: /next/i }));

    expect(screen.getByText("Frame 4")).toBeTruthy();
    expect(
      screen.getByRole("button", { name: /next/i }).hasAttribute("disabled")
    ).toBe(true);
  });
});

describe("an unnumbered frame", () => {
  it("is never given a number that another frame already has", async () => {
    // The UNIQUE constraint on (scan_id, frame_number) does not cover NULLs, so
    // this shape is reachable. Labelled by position, the third frame would read
    // "Frame 3" — the same as the second, over a different image.
    render(
      <ScanFrameViewer
        scan={scanWith([
          { n: 2, path: "a.png" },
          { n: 3, path: "b.png" },
          { n: null, path: "c.png" },
        ])}
      />
    );
    await waitFor(() => expect(screen.getByText("Frame 2")).toBeTruthy());

    fireEvent.click(screen.getByRole("button", { name: /next/i }));
    fireEvent.click(screen.getByRole("button", { name: /next/i }));

    expect(screen.queryByText("Frame 3 (unnumbered)")).toBeNull();
    expect(screen.getByText(/Unnumbered frame/)).toBeTruthy();
  });
});

/**
 * The empty states. A scientist acts differently on each of these, so one
 * sentence for all three is a wrong answer two times out of three.
 */
describe("when there is nothing to show", () => {
  it("says so plainly when the scan recorded no frames at all", async () => {
    render(<ScanFrameViewer scan={scanWith([])} />);

    await waitFor(() =>
      expect(screen.getByText(/No frames are recorded/)).toBeTruthy()
    );
  });

  it("distinguishes frames that are recorded but not yet uploaded", async () => {
    // Rows exist with no object_path — the pre-upload state. Reporting this as
    // "unable to retrieve" hides that the capture itself succeeded.
    const pending = scanWith([]);
    pending.cyl_images = [
      { id: 1, frame_number: 1, object_path: null, scan_id: 1 },
      { id: 2, frame_number: 2, object_path: null, scan_id: 1 },
    ];
    render(<ScanFrameViewer scan={pending} />);

    await waitFor(() =>
      expect(screen.getByText(/All 2 recorded frames are still uploading/)).toBeTruthy()
    );
  });


  // Paging holds the previous frame on screen rather than blanking, which means
  // the element is reused across a src change and the picture can briefly
  // disagree with the label. These pin the parts that make that safe.

  it("still reports a failure on a frame reached by paging, not just the first", async () => {
    // The img is deliberately unkeyed, so this failure lands on a reused
    // element rather than a freshly mounted one — the case the removed key
    // was thought to protect.
    const { container } = render(<ScanFrameViewer scan={THREE_FRAMES} />);
    const first = await waitFor(() => {
      const el = container.querySelector("img");
      if (!el) throw new Error("no img");
      return el;
    });
    fireEvent.load(first);

    fireEvent.click(screen.getByLabelText("Next frame"));
    await waitFor(() =>
      expect(container.querySelector("img")?.getAttribute("src")).toBe(
        "https://signed.test/f2.png"
      )
    );
    fireEvent.error(container.querySelector("img")!);

    expect(screen.getByText("Frame 2 could not be loaded.")).toBeTruthy();
  });

  it("dims the held frame until the next one loads", async () => {
    // Undimmed, a stale frame sits under a label naming a different one, and
    // consecutive rotation frames look alike enough to be misread.
    const { container } = render(<ScanFrameViewer scan={THREE_FRAMES} />);
    const img = await waitFor(() => {
      const el = container.querySelector("img");
      if (!el) throw new Error("no img");
      return el;
    });

    await waitFor(() => expect(img.className).toContain("opacity-40"));
    fireEvent.load(img);
    await waitFor(() => expect(img.className).not.toContain("opacity-40"));
  });

  it("holds the spinner back so a fast frame never flashes one", async () => {
    const { container } = render(<ScanFrameViewer scan={THREE_FRAMES} />);
    await waitFor(() => {
      if (!container.querySelector("img")) throw new Error("no img");
    });

    expect(screen.queryByRole("status")).toBeNull();
    await waitFor(() => expect(screen.getByRole("status")).toBeTruthy(), {
      timeout: 1000,
    });
  });

  it("clears the spinner once the frame loads", async () => {
    const { container } = render(<ScanFrameViewer scan={THREE_FRAMES} />);
    const img = await waitFor(() => {
      const el = container.querySelector("img");
      if (!el) throw new Error("no img");
      return el;
    });
    await waitFor(() => expect(screen.getByRole("status")).toBeTruthy(), {
      timeout: 1000,
    });

    fireEvent.load(img);

    await waitFor(() => expect(screen.queryByRole("status")).toBeNull());
  });

  it("fetches the neighbouring frames, and only those", async () => {
    const requested: string[] = [];
    const RealImage = window.Image;
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    (window as any).Image = class {
      set src(value: string) {
        requested.push(value);
      }
    };
    try {
      render(<ScanFrameViewer scan={THREE_FRAMES} />);
      await waitFor(() =>
        expect(requested).toContain("https://signed.test/f2.png")
      );
      // f3 is two away from the first frame — warming it would be wasted bytes
      // competing with the frame actually on screen.
      expect(requested).not.toContain("https://signed.test/f3.png");
    } finally {
      window.Image = RealImage;
    }
  });

  it("holds the frame box at a fixed height so paging cannot reflow the page", async () => {
    // The flicker was the box collapsing to each image in turn, which moved the
    // pager under the reader's cursor.
    const { container } = render(<ScanFrameViewer scan={THREE_FRAMES} />);
    const img = await waitFor(() => {
      const el = container.querySelector("img");
      if (!el) throw new Error("no img");
      return el;
    });

    expect((img.parentElement as HTMLElement).style.height).toBe("520px");
    expect(img.className).toContain("object-contain");
    expect(img.className).toContain("max-h-full");
  });

  it("distinguishes frames that exist but could not be signed", async () => {
    unsignable.add("s1.png");
    unsignable.add("s2.png");
    try {
      render(
        <ScanFrameViewer
          scan={scanWith([
            { n: 1, path: "s1.png" },
            { n: 2, path: "s2.png" },
          ])}
        />
      );
      await waitFor(() =>
        expect(screen.getByText(/could not be retrieved/)).toBeTruthy()
      );
    } finally {
      unsignable.clear();
    }
  });
});
