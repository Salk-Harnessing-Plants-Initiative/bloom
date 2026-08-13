import { describe, it, expect } from "vitest";
import {
  clampFrameIndex,
  frameLabel,
  missingFrameNote,
  orderedFrames,
  completenessWarning,
  signedUrlsByPath,
  usableUrl,
  type ScanFrame,
  type SignedUrlEntry,
} from "./plant-scan.helpers";

// A cyl_images row, defaulted so each test states only the fields it cares
// about (id / frame_number / object_path).
function frame(overrides: Partial<ScanFrame> & { id: number }): ScanFrame {
  return {
    date_scanned: null,
    frame_number: null,
    object_path: `frames/${overrides.id}.png`,
    scan_id: 1,
    status: null,
    uploaded_at: null,
    ...overrides,
  };
}

describe("orderedFrames", () => {
  it("orders frames by frame_number ascending", () => {
    const out = orderedFrames([
      frame({ id: 3, frame_number: 71 }),
      frame({ id: 1, frame_number: 0 }),
      frame({ id: 2, frame_number: 12 }),
    ]);

    expect(out.map((f) => f.frame_number)).toEqual([0, 12, 71]);
  });

  it("orders numerically, not lexicographically", () => {
    const out = orderedFrames([
      frame({ id: 1, frame_number: 10 }),
      frame({ id: 2, frame_number: 9 }),
      frame({ id: 3, frame_number: 100 }),
    ]);

    expect(out.map((f) => f.frame_number)).toEqual([9, 10, 100]);
  });

  it("drops rows with no object_path", () => {
    const out = orderedFrames([
      frame({ id: 1, frame_number: 0 }),
      frame({ id: 2, frame_number: 1, object_path: null }),
      frame({ id: 3, frame_number: 2, object_path: "" }),
      frame({ id: 4, frame_number: 3 }),
    ]);

    expect(out.map((f) => f.id)).toEqual([1, 4]);
  });

  it("sorts a null frame_number last, so it never displaces frame 1", () => {
    const out = orderedFrames([
      frame({ id: 1, frame_number: null }),
      frame({ id: 2, frame_number: 5 }),
      frame({ id: 3, frame_number: 1 }),
    ]);

    expect(out.map((f) => f.frame_number)).toEqual([1, 5, null]);
  });

  it("breaks frame_number ties by id, whatever order rows arrive in", () => {
    const rows = [
      frame({ id: 9, frame_number: 4 }),
      frame({ id: 2, frame_number: 4 }),
      frame({ id: 5, frame_number: 4 }),
    ];

    expect(orderedFrames(rows).map((f) => f.id)).toEqual([2, 5, 9]);
    expect(orderedFrames([...rows].reverse()).map((f) => f.id)).toEqual([
      2, 5, 9,
    ]);
  });

  it("orders all-null frame_numbers by id", () => {
    const out = orderedFrames([
      frame({ id: 7 }),
      frame({ id: 3 }),
      frame({ id: 5 }),
    ]);

    expect(out.map((f) => f.id)).toEqual([3, 5, 7]);
  });

  it("returns an empty list for null, undefined, or no rows", () => {
    expect(orderedFrames(null)).toEqual([]);
    expect(orderedFrames(undefined)).toEqual([]);
    expect(orderedFrames([])).toEqual([]);
  });

  it("returns an empty list when no row has an object_path", () => {
    const out = orderedFrames([
      frame({ id: 1, frame_number: 0, object_path: null }),
      frame({ id: 2, frame_number: 1, object_path: null }),
    ]);

    expect(out).toEqual([]);
  });

  it("keeps a shuffled rotation intact and in order", () => {
    const shuffled = [12, 0, 71, 40, 3, 55].map((n) =>
      frame({ id: n + 1, frame_number: n })
    );
    const out = orderedFrames(shuffled);

    expect(out).toHaveLength(6);
    expect(out.map((f) => f.frame_number)).toEqual([0, 3, 12, 40, 55, 71]);
    expect(out[0].object_path).toBe("frames/1.png");
  });

  it("does not mutate the caller's array", () => {
    const rows = [
      frame({ id: 1, frame_number: 9 }),
      frame({ id: 2, frame_number: 2 }),
    ];
    const before = [...rows];

    orderedFrames(rows);

    expect(rows).toEqual(before);
  });
});

describe("clampFrameIndex", () => {
  it("passes through an in-range index", () => {
    expect(clampFrameIndex(0, 72)).toBe(0);
    expect(clampFrameIndex(35, 72)).toBe(35);
    expect(clampFrameIndex(71, 72)).toBe(71);
  });

  it("clamps past the last frame", () => {
    expect(clampFrameIndex(72, 72)).toBe(71);
    expect(clampFrameIndex(9999, 72)).toBe(71);
  });

  it("clamps below the first frame", () => {
    expect(clampFrameIndex(-1, 72)).toBe(0);
    expect(clampFrameIndex(-9999, 72)).toBe(0);
  });

  it("holds a stale index in range when the frame list shrinks", () => {
    // Paged to frame 51 of 72, then the scan switches to a 4-frame one.
    expect(clampFrameIndex(50, 4)).toBe(3);
  });

  it("returns 0 for an empty frame list", () => {
    expect(clampFrameIndex(0, 0)).toBe(0);
    expect(clampFrameIndex(5, 0)).toBe(0);
    expect(clampFrameIndex(-5, -1)).toBe(0);
  });

  it("pins the only frame of a single-frame scan", () => {
    expect(clampFrameIndex(0, 1)).toBe(0);
    expect(clampFrameIndex(1, 1)).toBe(0);
  });

  it("returns 0 for a non-finite index", () => {
    expect(clampFrameIndex(NaN, 72)).toBe(0);
    expect(clampFrameIndex(Infinity, 72)).toBe(0);
  });
});

describe("frameLabel", () => {
  it("shows the frame's own frame_number, not its position", () => {
    // Frames 30 and 31 failed to upload, so list position 35 is frame 37.
    // Showing "35" would misstate the rotation angle.
    expect(frameLabel(frame({ id: 37, frame_number: 37 }), 34)).toBe("Frame 37");
  });

  it("does not renumber a frame to match its position", () => {
    expect(frameLabel(frame({ id: 1, frame_number: 0 }), 0)).toBe("Frame 0");
    expect(frameLabel(frame({ id: 9, frame_number: 71 }), 5)).toBe("Frame 71");
  });

  it("names the position without minting a frame number for it", () => {
    // Not "Frame 5 (unnumbered)": frame_number's UNIQUE constraint does not cover
    // NULLs, so position 5 can coincide with a real frame 5 on the same rotation
    // and label two different images identically.
    expect(frameLabel(frame({ id: 1 }), 4)).toBe("Unnumbered frame (5 in order)");
  });

  it("falls back when there is no frame at that index", () => {
    expect(frameLabel(undefined, 0)).toBe("Unnumbered frame (1 in order)");
  });

  it("never renders a bare `Frame <n>` for a row that has none", () => {
    for (const index of [0, 1, 2, 41]) {
      expect(frameLabel(frame({ id: 1 }), index)).not.toMatch(/^Frame \d+$/);
    }
  });
});

describe("missingFrameNote", () => {
  it("says nothing when every recorded frame is renderable", () => {
    expect(missingFrameNote(72, 72)).toBeNull();
    expect(missingFrameNote(0, 0)).toBeNull();
  });

  it("discloses frames that could not be shown", () => {
    expect(missingFrameNote(70, 72)).toBe(
      "Showing 70 of 72 frames — 2 not available."
    );
  });

  it("discloses the severe case a bare counter would hide", () => {
    // 1 of 72 renders with no pager at all — otherwise indistinguishable
    // from a healthy single-frame scan.
    expect(missingFrameNote(1, 72)).toBe(
      "Showing 1 of 72 frames — 71 not available."
    );
  });

  it("says nothing if the recorded count is somehow lower", () => {
    expect(missingFrameNote(72, 70)).toBeNull();
  });
});

describe("usableUrl", () => {
  it("passes a real signed URL through", () => {
    expect(usableUrl("https://x/1.png?token=a")).toBe("https://x/1.png?token=a");
  });

  it("maps a failed signing to null, not an empty src/href", () => {
    // The signing helpers report failure as "", which slips past a `!== null`
    // guard and makes the browser re-request the current document.
    expect(usableUrl("")).toBeNull();
    expect(usableUrl("   ")).toBeNull();
  });

  it("maps null and undefined to null", () => {
    expect(usableUrl(null)).toBeNull();
    expect(usableUrl(undefined)).toBeNull();
  });

  it("trims surrounding whitespace", () => {
    expect(usableUrl("  https://x/1.png  ")).toBe("https://x/1.png");
  });
});

describe("signedUrlsByPath", () => {
  const entries: SignedUrlEntry[] = [
    { path: "frames/1.png", signedUrl: "https://x/1?token=a" },
    { path: "frames/2.png", signedUrl: "https://x/2?token=b" },
  ];

  it("keys each signed URL by its storage path", () => {
    const urls = signedUrlsByPath(entries);

    expect(urls.get("frames/1.png")).toBe("https://x/1?token=a");
    expect(urls.get("frames/2.png")).toBe("https://x/2?token=b");
  });

  it("maps the same way whatever order the response arrives in", () => {
    expect(signedUrlsByPath([...entries].reverse())).toEqual(
      signedUrlsByPath(entries)
    );
  });

  it("omits entries that failed to sign, keeping the rest", () => {
    const urls = signedUrlsByPath([
      { path: "frames/1.png", signedUrl: "https://x/1?token=a" },
      { path: "frames/2.png", signedUrl: null },
      { path: "frames/3.png", signedUrl: "" },
      { path: null, signedUrl: "https://x/orphan" },
      { path: "frames/4.png" },
    ]);

    expect(urls.size).toBe(1);
    expect(urls.get("frames/1.png")).toBe("https://x/1?token=a");
    expect(urls.has("frames/2.png")).toBe(false);
    expect(urls.has("frames/3.png")).toBe(false);
  });

  it("returns an empty map for null, undefined, or no entries", () => {
    expect(signedUrlsByPath(null).size).toBe(0);
    expect(signedUrlsByPath(undefined).size).toBe(0);
    expect(signedUrlsByPath([]).size).toBe(0);
  });
});

describe("completenessWarning", () => {
  // The single source of the signal that gates the confirm step. Deleting its body used to
  // leave the whole suite green.
  it("is null when every recorded frame is renderable and consecutive", () => {
    const images = [1, 2, 3].map((n) => frame({ id: n, frame_number: n }));

    expect(completenessWarning(images)).toBeNull();
  });

  it("reports rows that cannot be rendered", () => {
    const images = [
      frame({ id: 1, frame_number: 1 }),
      frame({ id: 2, frame_number: 2, object_path: null }),
    ];

    expect(completenessWarning(images)).toContain("Showing 1 of 2 frames");
  });

  it("says nothing about skipped frame numbers", () => {
    // Frames 1,2,4 — a hole in the numbering, but every recorded frame is there to
    // show. A second count against a different denominator only confuses.
    const images = [1, 2, 4].map((n) => frame({ id: n, frame_number: n }));

    expect(completenessWarning(images)).toBeNull();
  });

  it("counts only rows whose image is not available", () => {
    const images = [
      frame({ id: 1, frame_number: 1 }),
      frame({ id: 2, frame_number: 2, object_path: null }),
      frame({ id: 4, frame_number: 4 }),
    ];

    expect(completenessWarning(images)).toBe(
      "Showing 2 of 3 frames — 1 not available."
    );
  });

  it("is null for a scan with no images at all", () => {
    expect(completenessWarning([])).toBeNull();
    expect(completenessWarning(null)).toBeNull();
    expect(completenessWarning(undefined)).toBeNull();
  });
});
