import { describe, expect, it } from "vitest";
import { encodableFrameCount } from "./plate-frames";

const withImage = (path: string) => ({ gravi_images: { object_path: path } });

describe("encodableFrameCount", () => {
  it("counts captures that have an image", () => {
    expect(encodableFrameCount([withImage("a.tif"), withImage("b.tif")])).toBe(2);
  });

  it("does not count a capture whose image row is missing", () => {
    // The encoder's `!inner` join drops this row, so the button must too.
    expect(
      encodableFrameCount([withImage("a.tif"), { gravi_images: null }]),
    ).toBe(1);
  });

  it("does not count a capture whose image row has no path", () => {
    expect(
      encodableFrameCount([
        withImage("a.tif"),
        { gravi_images: { object_path: "" } },
      ]),
    ).toBe(1);
  });

  it("is zero for a plate with no captures", () => {
    expect(encodableFrameCount([])).toBe(0);
  });
});
