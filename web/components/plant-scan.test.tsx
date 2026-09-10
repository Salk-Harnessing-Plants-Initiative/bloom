// @vitest-environment jsdom
/**
 * PlantScan is reused across scans rather than remounted — the boxplot swaps
 * its `scan` prop as the reader clicks between plants. These cover what that
 * reuse can do to a signed URL held in state, which no helper test can reach.
 */

import { afterEach, describe, expect, it, vi } from "vitest";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";

import PlantScan from "./plant-scan";

// Images always sign. Videos sign for scan 5 only — a mock where no scan has a video
// cannot tell whether the URL is dropped when the scan changes.
vi.mock("@/lib/supabase/client", () => ({
  createClientSupabaseClient: () => ({
    storage: {
      from: (bucket: string) => ({
        createSignedUrl: async (path: string) => {
          if (bucket === "images") {
            return { data: { signedUrl: `https://signed.test/${path}` } };
          }
          return path === "cyl-videos/5.mp4"
            ? { data: { signedUrl: "https://signed.test/cyl-videos/5.mp4" } }
            : { data: null };
        },
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

  it("shows the video link only for a scan that has one", async () => {
    // The suite had no video coverage at all: the mock signed no videos, so `videoUrl` was
    // always null. Scan 5 has one here and scan 6 does not.
    // `imageIsLoaded` gates the link and jsdom never fires load on its own.
    const { container, rerender } = render(
      <PlantScan scan={scanWith(5, ["a.png"])} />
    );
    await waitFor(() => expect(container.querySelector("img")).not.toBeNull());
    fireEvent.load(container.querySelector("img")!);
    await screen.findByLabelText(/Open .* video/);

    rerender(<PlantScan scan={scanWith(6, ["b.png"])} />);
    await waitFor(() =>
      expect(container.querySelector("img")?.getAttribute("src")).toBe(
        "https://signed.test/b.png"
      )
    );
    fireEvent.load(container.querySelector("img")!);

    await waitFor(() =>
      expect(screen.queryByLabelText(/Open .* video/)).toBeNull()
    );
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

/**
 * The affordance and naming this component grew: a screen reader must be able
 * to tell one scan from another, and a keyboard user must be able to reach the
 * video. Both are attribute-level and would otherwise regress in silence.
 */
describe("PlantScan naming and reachability", () => {
  it("names the image for a screen reader instead of leaking the signed URL", async () => {
    render(<PlantScan scan={scanWith(1, ["a.png"])} />);

    const img = await screen.findByAltText("Scan thumbnail");
    // The src is a signed URL with a token in it; an unnamed image gets read out.
    expect(img.getAttribute("src")).toContain("https://signed.test/");
  });

  it("lets the caller name the scan without drawing the visible badge", async () => {
    render(
      <PlantScan scan={scanWith(1, ["a.png"])} altText="Cylinder scan, day 12" />
    );

    expect(await screen.findByAltText("Cylinder scan, day 12")).toBeTruthy();
    // `label` is the prop that draws a badge; altText must not.
    expect(screen.queryByText("Cylinder scan, day 12")).toBeNull();
  });

  it("names the link by the scan, so a row of days is not a row of identical links", async () => {
    render(
      <PlantScan
        scan={scanWith(1, ["a.png"])}
        href="/app/phenotypes/1/2/3/4/1"
        altText="Cylinder scan, day 12"
      />
    );

    expect(
      await screen.findByRole("link", { name: "Cylinder scan, day 12" })
    ).toBeTruthy();
  });

  it("shows the scan video badge outright, not on hover", async () => {
    const { container } = render(<PlantScan scan={scanWith(5, ["a.png"])} />);

    const img = await screen.findByAltText("Scan thumbnail");
    fireEvent.load(img);

    const link = await screen.findByLabelText(/Open .* video/);
    // A badge that only appears on hover is a badge nobody finds — and
    // `hidden` is display:none, which also drops it out of the tab order.
    expect(link.className).not.toContain("hidden");
    expect(link.className).not.toContain("opacity-0");
    // The padded chip is the anchor itself, so all of it is clickable.
    expect(link.className).toContain("p-1");
    expect(container.querySelector(".group")?.contains(link)).toBe(true);
  });

  it("names the video link by the scan, not identically on every tile", async () => {
    render(
      <PlantScan scan={scanWith(5, ["a.png"])} altText="Cylinder scan, day 21" />
    );
    fireEvent.load(await screen.findByAltText("Cylinder scan, day 21"));

    // A grid of these all called "Open scan video" is a link list with no way
    // to tell one day from another.
    expect(
      await screen.findByLabelText(
        "Open Cylinder scan, day 21 video in a new tab"
      )
    ).toBeTruthy();
  });

  it("shows no video badge for a scan that has no video", async () => {
    render(<PlantScan scan={scanWith(1, ["a.png"])} />);
    fireEvent.load(await screen.findByAltText("Scan thumbnail"));

    expect(screen.queryByLabelText(/Open .* video/)).toBeNull();
  });

  it("hints at the click for keyboard focus, not just the mouse", async () => {
    const { container } = render(
      <PlantScan scan={scanWith(1, ["a.png"])} href="/app/phenotypes/1/2/3/4/1" />
    );
    await screen.findByAltText("Scan thumbnail");

    const box = container.querySelector(".group > div") as HTMLElement;
    expect(box.className).toContain("group-hover:border-lime-700");
    expect(box.className).toContain("group-focus-within:border-lime-700");
  });

  it("sizes the box from the height prop", async () => {
    // The height was an interpolated Tailwind class, which the scanner never
    // emits — the boxplot's 250px box was rendering with no height at all.
    const { container } = render(
      <PlantScan scan={scanWith(1, ["a.png"])} height={250} />
    );
    await screen.findByAltText("Scan thumbnail");

    const box = container.querySelector(".group > div") as HTMLElement;
    expect(box.style.height).toBe("250px");
  });
});
