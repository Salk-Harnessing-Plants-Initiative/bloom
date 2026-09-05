/** How many of a plate's captures will actually become video frames.
 *
 * The page reads scans with a loose join, so a capture whose image row is
 * missing still comes back. The encoder joins with `!inner` and never sees it.
 * Counting scans here would have the button promise frames the video will not
 * contain — "24 new" against a video that gains 23.
 */
export function encodableFrameCount(
  scans: { gravi_images: { object_path: string } | null }[],
): number {
  return scans.filter((scan) => scan.gravi_images?.object_path).length;
}
