import { describe, it, expect } from "vitest";
import { VIDEOS_BUCKET, scanVideoPath } from "./scan-video-path";

// Lives here rather than in scan-video.test.ts so the module has coverage that
// does not depend on the server-only module re-exporting it. The Python side of
// the same agreement is tests/unit/test_cyl_video_path_agreement.py.
describe("scanVideoPath", () => {
  it("matches the key services/workflows/video.py writes", () => {
    expect(scanVideoPath(4207)).toBe("cyl-videos/4207.mp4");
  });

  it("names the bucket the encoder uploads to", () => {
    expect(VIDEOS_BUCKET).toBe("videos");
  });
});
