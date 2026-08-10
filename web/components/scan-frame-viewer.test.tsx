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

  it("discloses a gap in the recorded frame numbers", async () => {
    render(
      <ScanFrameViewer
        scan={scanWith([
          { n: 1, path: "f1.png" },
          { n: 2, path: "f2.png" },
          { n: 4, path: "f4.png" },
        ])}
      />
    );

    await screen.findByText(/1 frame missing from this rotation/);
  });

  it("says nothing about gaps on a consecutive rotation", async () => {
    render(<ScanFrameViewer scan={THREE_FRAMES} />);

    await screen.findByText("Frame 1");
    expect(screen.queryByText(/missing from this rotation/)).toBeNull();
  });
});
