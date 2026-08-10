// @vitest-environment jsdom
/**
 * PlantScan is reused across scans rather than remounted — the boxplot swaps
 * its `scan` prop as the reader clicks between plants. These cover what that
 * reuse can do to a signed URL held in state, which no helper test can reach.
 */

import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";

import PlantScan from "./plant-scan";

// Images sign; videos don't (most scans have none), matching the common case.
vi.mock("@/lib/supabase/client", () => ({
  createClientSupabaseClient: () => ({
    storage: {
      from: (bucket: string) => ({
        createSignedUrl: async (path: string) =>
          bucket === "images"
            ? { data: { signedUrl: `https://signed.test/${path}` } }
            : { data: null },
      }),
    },
  }),
}));

// A scan carrying one frame per supplied path; a null path is a row whose
// image never uploaded.
function scanWith(id: number, paths: (string | null)[]) {
  return {
    id,
    cyl_images: paths.map((object_path, i) => ({
      id: i + 1,
      frame_number: i + 1,
      object_path,
      scan_id: id,
      date_scanned: null,
      status: null,
      uploaded_at: null,
    })),
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
  } as any;
}

afterEach(cleanup);

describe("PlantScan across a scan change", () => {
  it("shows the scan's own image", async () => {
    const { container } = render(<PlantScan scan={scanWith(1, ["a.png"])} />);

    await waitFor(() =>
      expect(container.querySelector("img")?.getAttribute("src")).toBe(
        "https://signed.test/a.png"
      )
    );
  });

  it("does not keep the previous scan's image when the next has none", async () => {
    // The boxplot renders one instance and swaps the prop; plant A's image must
    // not survive under plant B's label and href.
    const { container, rerender } = render(
      <PlantScan scan={scanWith(1, ["a.png"])} label="Plant: A" />
    );
    await waitFor(() => expect(container.querySelector("img")).not.toBeNull());

    rerender(<PlantScan scan={scanWith(2, [null])} label="Plant: B" />);

    await screen.findByText("Unable to retrieve scan image.");
    expect(container.querySelector("img")).toBeNull();
  });

  it("swaps to the next scan's image rather than leaving the old one", async () => {
    const { container, rerender } = render(
      <PlantScan scan={scanWith(1, ["a.png"])} />
    );
    await waitFor(() =>
      expect(container.querySelector("img")?.getAttribute("src")).toBe(
        "https://signed.test/a.png"
      )
    );

    rerender(<PlantScan scan={scanWith(2, ["b.png"])} />);

    await waitFor(() =>
      expect(container.querySelector("img")?.getAttribute("src")).toBe(
        "https://signed.test/b.png"
      )
    );
  });
});
