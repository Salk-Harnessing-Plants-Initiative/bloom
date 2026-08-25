/**
 * A plate id is free text and ends up as a path segment, so these cover what
 * must never become one.
 */

import { describe, expect, it } from "vitest";

import {
  GRAVISCAN_IMAGES_BUCKET,
  GRAVISCAN_VIDEOS_BUCKET,
  isValidPlateId,
  plateVideoPath,
  waveSegment,
} from "./plate-video-path";

describe("plateVideoPath", () => {
  it("builds the key the existing videos already live under", () => {
    // Objects already exist under this layout; changing it orphans them.
    expect(plateVideoPath(12, 3, "Plate_9")).toBe("12/wave-3/Plate_9.mp4");
  });

  it("gives a plate with no wave its own segment", () => {
    // An empty segment would give `12//Plate_9.mp4`.
    expect(plateVideoPath(12, null, "Plate_9")).toBe(
      "12/wave-none/Plate_9.mp4",
    );
  });

  it("keeps waves apart, because plate ids repeat across them", () => {
    const wave2 = plateVideoPath(12, 2, "Plate_9");
    const wave3 = plateVideoPath(12, 3, "Plate_9");
    expect(wave2).not.toBe(wave3);
  });

  it("treats wave zero as a wave, not as absent", () => {
    // The scanner app sends 0 when no wave is set, so 0 arrives in practice.
    expect(plateVideoPath(12, 0, "Plate_9")).toBe("12/wave-0/Plate_9.mp4");
  });

  it("refuses a plate id carrying a path separator", () => {
    expect(plateVideoPath(12, 3, "../../secrets")).toBeNull();
    expect(plateVideoPath(12, 3, "a/b")).toBeNull();
    expect(plateVideoPath(12, 3, "a\\b")).toBeNull();
  });

  it("refuses a leading dot, so `..` can never form", () => {
    expect(plateVideoPath(12, 3, ".hidden")).toBeNull();
    expect(plateVideoPath(12, 3, "..")).toBeNull();
  });

  it("refuses an empty or overlong plate id", () => {
    expect(plateVideoPath(12, 3, "")).toBeNull();
    expect(plateVideoPath(12, 3, "P".repeat(65))).toBeNull();
    expect(plateVideoPath(12, 3, "P".repeat(64))).not.toBeNull();
  });

  it("refuses unicode and whitespace", () => {
    expect(plateVideoPath(12, 3, "Plate_9 ")).toBeNull();
    expect(plateVideoPath(12, 3, "Plate 9")).toBeNull();
    expect(plateVideoPath(12, 3, "Platé_9")).toBeNull();
    expect(plateVideoPath(12, 3, "Plate_9\n")).toBeNull();
  });

  it("refuses an experiment id that is not a positive integer", () => {
    expect(plateVideoPath(0, 3, "Plate_9")).toBeNull();
    expect(plateVideoPath(-1, 3, "Plate_9")).toBeNull();
    expect(plateVideoPath(1.5, 3, "Plate_9")).toBeNull();
    expect(plateVideoPath(Number.NaN, 3, "Plate_9")).toBeNull();
  });

  it("refuses a wave that is not a non-negative integer", () => {
    // `wave-NaN` and `wave--1` are addressable but meaningless.
    expect(plateVideoPath(12, Number.NaN, "Plate_9")).toBeNull();
    expect(plateVideoPath(12, -1, "Plate_9")).toBeNull();
    expect(plateVideoPath(12, 1.5, "Plate_9")).toBeNull();
  });
});

describe("isValidPlateId", () => {
  it("accepts the shapes the scanner actually produces", () => {
    for (const id of ["Plate_9", "Plate_13", "PLATE-001", "P1", "9"]) {
      expect(isValidPlateId(id)).toBe(true);
    }
  });

  it("rejects what must never reach a path", () => {
    for (const id of ["", "..", "./x", "a/b", "a b", "-lead", "_lead"]) {
      expect(isValidPlateId(id)).toBe(false);
    }
  });
});

describe("waveSegment", () => {
  it("names the absent wave rather than leaving it blank", () => {
    expect(waveSegment(null)).toBe("wave-none");
  });

  it("returns null for a wave it cannot name", () => {
    expect(waveSegment(Number.NaN)).toBeNull();
    expect(waveSegment(-1)).toBeNull();
  });
});

describe("buckets", () => {
  it("names both graviscan buckets, so callers do not re-hardcode them", () => {
    expect(GRAVISCAN_VIDEOS_BUCKET).toBe("graviscan-videos");
    expect(GRAVISCAN_IMAGES_BUCKET).toBe("graviscan-images");
  });
});
