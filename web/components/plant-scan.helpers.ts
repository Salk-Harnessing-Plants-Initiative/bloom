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

// Signed URLs keyed by storage path. Keying by path (rather than trusting the
// response order) means a partial or reordered batch response can't shift a
// URL onto the wrong frame — a missing path simply has no URL.
export function signedUrlsByPath(
  entries: SignedUrlEntry[] | null | undefined
): Map<string, string> {
  const byPath = new Map<string, string>();
  for (const entry of entries ?? []) {
    if (entry?.path && entry?.signedUrl) byPath.set(entry.path, entry.signedUrl);
  }
  return byPath;
}
