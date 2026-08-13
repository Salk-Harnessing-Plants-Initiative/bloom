// Pure frame-list helpers for the cylinder scan viewer, split out so the
// ordering and paging rules are unit-testable without rendering the client
// component.

import type { CylScanWithImages } from "@/lib/custom.types";

// One cyl_images row as the scan detail query returns it.
export type ScanFrame = CylScanWithImages["cyl_images"][number];

// A frame we can actually render: it carries a storage path to sign.
export type RenderableFrame = ScanFrame & { object_path: string };

// One entry of a Supabase `createSignedUrls` response.
export type SignedUrlEntry = {
  path?: string | null;
  signedUrl?: string | null;
};

// Renderable frames ordered by frame_number ascending. Rows without an
// object_path can't be signed, so they are dropped. Rows with a null
// frame_number sort last rather than to the front, so an unnumbered row never
// displaces frame 1; ties break by id so the order is stable whatever order
// the rows arrived in.
export function orderedFrames(
  images: ScanFrame[] | null | undefined
): RenderableFrame[] {
  const renderable = (images ?? []).filter(
    (img): img is RenderableFrame => Boolean(img?.object_path)
  );

  return renderable.sort((a, b) => {
    const an = a.frame_number;
    const bn = b.frame_number;
    if (an === null && bn === null) return a.id - b.id;
    if (an === null) return 1;
    if (bn === null) return -1;
    if (an !== bn) return an - bn;
    return a.id - b.id;
  });
}

// Frame index held inside [0, total - 1]. Returns 0 when there are no frames,
// so callers can index without a bounds check — `frames[0]` of an empty list is
// undefined either way.
export function clampFrameIndex(index: number, total: number): number {
  if (total <= 0) return 0;
  if (!Number.isFinite(index)) return 0;
  return Math.min(Math.max(Math.trunc(index), 0), total - 1);
}

// A frame's identity is its frame_number — on a rotation that fixes the angle.
// Its position in the list is not the same thing: the two diverge as soon as a
// frame is missing, so never show the position alone.
export function frameLabel(
  frame: ScanFrame | undefined,
  index: number
): string {
  const number = frame?.frame_number;
  if (number === null || number === undefined) {
    // Never `Frame ${index + 1}`: the UNIQUE constraint on (scan_id, frame_number)
    // does not cover NULLs, so a position can coincide with a real frame's number and
    // print the same label twice on one rotation, over two different images.
    return `Unnumbered frame (${index + 1} in order)`;
  }
  return `Frame ${number}`;
}

// Says so when the scan records more frames than can be shown — otherwise a
// part-uploaded scan looks identical to a complete one.
export function missingFrameNote(
  renderable: number,
  recorded: number
): string | null {
  if (recorded <= renderable) return null;
  return `Showing ${renderable} of ${recorded} frames — ${
    recorded - renderable
  } not available.`;
}

// What the viewer says about this scan when frames are missing, or null when every
// recorded frame is there. The same call the viewer makes, so the two cannot disagree.
export function completenessWarning(
  images: ScanFrame[] | null | undefined
): string | null {
  return missingFrameNote(orderedFrames(images).length, images?.length ?? 0);
}

// A signed URL we can actually put in an href/src, or null. Signing helpers
// report failure as an empty string, which passes a `!== null` guard and lands
// in the DOM as `src=""` / `href=""` — the browser then resolves that against
// the current document and re-requests the page. Normalising to null keeps
// those guards meaningful.
export function usableUrl(url: string | null | undefined): string | null {
  const trimmed = url?.trim();
  return trimmed ? trimmed : null;
}

// Signed URLs keyed by storage path. Keying by path (rather than trusting the
// response order) means a partial or reordered batch response can't shift a
// URL onto the wrong frame — a missing path simply has no URL.
export function signedUrlsByPath(
  entries: SignedUrlEntry[] | null | undefined
): Map<string, string> {
  const byPath = new Map<string, string>();
  for (const entry of entries ?? []) {
    const url = usableUrl(entry?.signedUrl);
    if (entry?.path && url) byPath.set(entry.path, url);
  }
  return byPath;
}
