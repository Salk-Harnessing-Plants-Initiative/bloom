"use client";

import { useEffect, useMemo, useState } from "react";
import { createClientSupabaseClient } from "@/lib/supabase/client";
import { CylScanWithImages } from "@/lib/custom.types";
import {
  clampFrameIndex,
  frameLabel,
  missingFrameNote,
  orderedFrames,
  signedUrlsByPath,
} from "./plant-scan.helpers";

// Long enough to page through a scan's frames without re-signing.
const FRAME_URL_TTL = 3600;

// Sign every frame in one request, keyed by path so a partial or reordered
// response can't shift frames out of position.
async function getFrameUrls(paths: string[]) {
  const supabase = createClientSupabaseClient();

  const { data } = await supabase.storage
    .from("images")
    .createSignedUrls(paths, FRAME_URL_TTL);

  return signedUrlsByPath(data);
}

// Frame-by-frame view of one cyl scan. Knows nothing about videos: paging is
// local state here, so nothing outside can reset the reader's position.
export default function ScanFrameViewer({ scan }: { scan: CylScanWithImages }) {
  const [frameUrls, setFrameUrls] = useState<Map<string, string>>(new Map());
  const [loading, setLoading] = useState<boolean>(true);
  const [imageIsLoaded, setImageIsLoaded] = useState<boolean>(false);
  const [requestedIndex, setRequestedIndex] = useState<number>(0);

  // Keyed on the scan id, not the object: a refetch that returns the same scan
  // must not look like a different one, or it would reset the reader's frame.
  const scanId = scan?.id;
  const frames = useMemo(
    () => orderedFrames(scan?.cyl_images),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [scanId]
  );
  const total = frames.length;

  // Clamp on read, so a stale index can never point past the end even for the
  // render before the reset effect runs.
  const frameIndex = clampFrameIndex(requestedIndex, total);
  const currentPath = frames[frameIndex]?.object_path ?? null;

  useEffect(() => {
    setRequestedIndex(0);
    setImageIsLoaded(false);
  }, [frames]);

  useEffect(() => {
    if (total === 0) {
      setLoading(false);
      return;
    }
    let active = true;
    setLoading(true);
    getFrameUrls(frames.map((f) => f.object_path))
      .then((urls) => {
        if (!active) return;
        setFrameUrls(urls);
        setLoading(false);
      })
      .catch(() => {
        if (!active) return;
        setFrameUrls(new Map());
        setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [frames, total]);

  const objectUrl = currentPath ? frameUrls.get(currentPath) ?? null : null;

  // Nothing to show: no renderable frame, or signing failed for every frame.
  if (!loading && (total === 0 || frameUrls.size === 0)) {
    return (
      <div className="rounded-lg border-2 border-dashed border-stone-300 bg-stone-50 px-4 py-6 text-sm text-stone-500 italic">
        Unable to retrieve scan image.
      </div>
    );
  }

  const shortfall = missingFrameNote(total, scan?.cyl_images?.length ?? 0);
  const label = frameLabel(frames[frameIndex], frameIndex);

  return (
    <div>
      <div
        className={
          "relative bg-stone-300 box-content rounded-lg border-4 border-neutral-300 flex flex-col" +
          (loading ? " animate-pulse" : "")
        }
      >
        {objectUrl !== null ? (
          <img
            src={objectUrl}
            className="rounded-md"
            onLoad={() => setImageIsLoaded(true)}
            onError={() => setImageIsLoaded(false)}
          />
        ) : !loading ? (
          // This one frame didn't sign — keep the pager so the rest stay reachable.
          <div className="px-4 py-6 text-sm text-stone-500 italic">
            {label} could not be loaded.
          </div>
        ) : null}
      </div>

      <div className="mt-2 flex items-center justify-center gap-4">
        {total > 1 && (
          <button
            type="button"
            onClick={() =>
              setRequestedIndex(clampFrameIndex(frameIndex - 1, total))
            }
            disabled={frameIndex === 0}
            aria-label="Previous frame"
            className="rounded-md border border-stone-300 bg-white px-3 py-1 text-lg leading-none text-stone-600 hover:bg-stone-100 disabled:opacity-40 disabled:hover:bg-white"
          >
            ‹
          </button>
        )}
        {/* Always shown, so a single-frame scan still names the frame it shows. */}
        <span className="text-sm tabular-nums text-stone-500" aria-live="polite">
          {label}
          {total > 1 && (
            <span className="ml-2 text-stone-400">
              {frameIndex + 1} / {total}
            </span>
          )}
        </span>
        {total > 1 && (
          <button
            type="button"
            onClick={() =>
              setRequestedIndex(clampFrameIndex(frameIndex + 1, total))
            }
            disabled={frameIndex === total - 1}
            aria-label="Next frame"
            className="rounded-md border border-stone-300 bg-white px-3 py-1 text-lg leading-none text-stone-600 hover:bg-stone-100 disabled:opacity-40 disabled:hover:bg-white"
          >
            ›
          </button>
        )}
      </div>

      {shortfall && (
        <p className="mt-2 text-center text-sm text-stone-500 italic">
          {shortfall}
        </p>
      )}
    </div>
  );
}
