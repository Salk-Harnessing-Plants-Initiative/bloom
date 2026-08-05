"use client";

import { createClientSupabaseClient } from "@/lib/supabase/client";
import { useEffect, useMemo, useState } from "react";
import { CylScanWithImages } from "@/lib/custom.types";
import {
  clampFrameIndex,
  frameLabel,
  missingFrameNote,
  orderedFrames,
  signedUrlsByPath,
  usableUrl,
} from "./plant-scan.helpers";
import Link from "next/link";

// Long enough to page through a scan's frames without re-signing. Matches the
// video URL's TTL below.
const FRAME_URL_TTL = 3600;

// Thumbnails need a transform (resize), which only the single-path API takes.
async function getImageUrl(path: string, thumb: boolean, height: number) {
  const supabase = createClientSupabaseClient();

  const { data } = await supabase.storage.from("images").createSignedUrl(
    path,
    FRAME_URL_TTL,
    thumb
      ? {
          transform: {
            height: height,
            quality: 100,
          },
        }
      : {}
  );

  return usableUrl(data?.signedUrl);
}

// Sign every frame of a scan in one request, keyed by path so a partial or
// reordered response can't shift frames out of position.
async function getFrameUrls(paths: string[]) {
  const supabase = createClientSupabaseClient();

  const { data } = await supabase.storage
    .from("images")
    .createSignedUrls(paths, FRAME_URL_TTL);

  return signedUrlsByPath(data);
}

// Null when the scan has no stored video — most don't, and an empty string here
// would render the download icon pointing at the current page.
async function getVideoUrl(scan: CylScanWithImages) {
  const supabase = createClientSupabaseClient();

  const path = `cyl-videos/${scan.id}.mp4`;

  const { data } = await supabase.storage
    .from("videos")
    .createSignedUrl(path, FRAME_URL_TTL);

  return usableUrl(data?.signedUrl);
}

const defaultHeight = 100;

export default function PlantScan({
  scan,
  thumb,
  href,
  target,
  height,
  label,
}: {
  scan: CylScanWithImages;
  thumb: boolean;
  href?: string;
  target?: string;
  height?: number;
  label?: string;
}) {
  const [frameUrls, setFrameUrls] = useState<Map<string, string>>(new Map());
  const [videoUrl, setVideoUrl] = useState<string | null>(null);
  const [loading, setLoading] = useState<boolean>(true);
  const [imageIsLoaded, setImageIsLoaded] = useState<boolean>(false);
  const [requestedIndex, setRequestedIndex] = useState<number>(0);

  // Renderable frames (have an object_path), ordered by frame_number.
  const frames = useMemo(() => orderedFrames(scan?.cyl_images), [scan]);
  const total = frames.length;

  // Clamp on read, so a stale index from a shorter frame list can never point
  // past the end even for the render before the reset effect below runs.
  const frameIndex = clampFrameIndex(requestedIndex, total);
  const currentPath = frames[frameIndex]?.object_path ?? null;

  // Reset to the first frame when the scan (frame set) changes.
  useEffect(() => {
    setRequestedIndex(0);
  }, [frames]);

  // Detail view: sign every frame in one request, so paging costs no further
  // round-trips and the URL for a frame is ready before it is asked for.
  useEffect(() => {
    if (thumb) return;
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
  }, [thumb, frames, total]);

  // Thumbnail: a single frame, signed with the resize transform.
  useEffect(() => {
    if (!thumb) return;
    if (currentPath === null) {
      setLoading(false);
      return;
    }
    let active = true;
    setLoading(true);
    getImageUrl(currentPath, true, height || defaultHeight)
      .then((url) => {
        if (!active) return;
        setFrameUrls(url ? new Map([[currentPath, url]]) : new Map());
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
  }, [thumb, currentPath, height]);

  const objectUrl = currentPath ? frameUrls.get(currentPath) ?? null : null;

  // This instance is reused across scans, so drop the previous scan's video.
  useEffect(() => {
    let active = true;
    setVideoUrl(null);
    setImageIsLoaded(false);
    getVideoUrl(scan).then((url) => {
      if (active) setVideoUrl(url);
    });
    return () => {
      active = false;
    };
  }, [scan]);

  // Nothing to show: no renderable frame, or signing failed for every frame.
  if (!loading && (total === 0 || frameUrls.size === 0)) {
    return (
      <div className="rounded-lg border-2 border-dashed border-stone-300 bg-stone-50 px-4 py-6 text-sm text-stone-500 italic">
        Unable to retrieve scan image.
      </div>
    );
  }

  const showNav = !thumb && total > 1;
  // Frames the scan records vs frames we can render — disclosed, not hidden.
  const shortfall = missingFrameNote(total, scan?.cyl_images?.length ?? 0);

  return (
    <div className="group">
      <div
        className={
          "relative bg-stone-300 box-content rounded-lg border-4 border-neutral-300" +
          (thumb ? ` h-[${height || defaultHeight}px]` : " flex flex-col") +
          (objectUrl === null || loading ? " animate-pulse" : "")
        }
      >
        {imageIsLoaded && (
          <div className="p-1 rounded-md bg-stone-50 border absolute hidden left-1 top-1 group-hover:block text-lime-700 opacity-70 hover:opacity-90">
            {videoUrl !== null ? (
              <a href={videoUrl} target="_blank">
                <svg
                  xmlns="http://www.w3.org/2000/svg"
                  fill="none"
                  viewBox="0 0 24 24"
                  strokeWidth={1.5}
                  stroke="currentColor"
                  className="w-6 h-6"
                >
                  <path
                    strokeLinecap="round"
                    strokeLinejoin="round"
                    d="M3.375 19.5h17.25m-17.25 0a1.125 1.125 0 0 1-1.125-1.125M3.375 19.5h1.5C5.496 19.5 6 18.996 6 18.375m-3.75 0V5.625m0 12.75v-1.5c0-.621.504-1.125 1.125-1.125m18.375 2.625V5.625m0 12.75c0 .621-.504 1.125-1.125 1.125m1.125-1.125v-1.5c0-.621-.504-1.125-1.125-1.125m0 3.75h-1.5A1.125 1.125 0 0 1 18 18.375M20.625 4.5H3.375m17.25 0c.621 0 1.125.504 1.125 1.125M20.625 4.5h-1.5C18.504 4.5 18 5.004 18 5.625m3.75 0v1.5c0 .621-.504 1.125-1.125 1.125M3.375 4.5c-.621 0-1.125.504-1.125 1.125M3.375 4.5h1.5C5.496 4.5 6 5.004 6 5.625m-3.75 0v1.5c0 .621.504 1.125 1.125 1.125m0 0h1.5m-1.5 0c-.621 0-1.125.504-1.125 1.125v1.5c0 .621.504 1.125 1.125 1.125m1.5-3.75C5.496 8.25 6 7.746 6 7.125v-1.5M4.875 8.25C5.496 8.25 6 8.754 6 9.375v1.5m0-5.25v5.25m0-5.25C6 5.004 6.504 4.5 7.125 4.5h9.75c.621 0 1.125.504 1.125 1.125m1.125 2.625h1.5m-1.5 0A1.125 1.125 0 0 1 18 7.125v-1.5m1.125 2.625c-.621 0-1.125.504-1.125 1.125v1.5m2.625-2.625c.621 0 1.125.504 1.125 1.125v1.5c0 .621-.504 1.125-1.125 1.125M18 5.625v5.25M7.125 12h9.75m-9.75 0A1.125 1.125 0 0 1 6 10.875M7.125 12C6.504 12 6 12.504 6 13.125m0-2.25C6 11.496 5.496 12 4.875 12M18 10.875c0 .621-.504 1.125-1.125 1.125M18 10.875c0 .621.504 1.125 1.125 1.125m-2.25 0c.621 0 1.125.504 1.125 1.125m-12 5.25v-5.25m0 5.25c0 .621.504 1.125 1.125 1.125h9.75c.621 0 1.125-.504 1.125-1.125m-12 0v-1.5c0-.621-.504-1.125-1.125-1.125M18 18.375v-5.25m0 5.25v-1.5c0-.621.504-1.125 1.125-1.125M18 13.125v1.5c0 .621.504 1.125 1.125 1.125M18 13.125c0-.621.504-1.125 1.125-1.125M6 13.125v1.5c0 .621-.504 1.125-1.125 1.125M6 13.125C6 12.504 5.496 12 4.875 12m-1.5 0h1.5m-1.5 0c-.621 0-1.125.504-1.125 1.125v1.5c0 .621.504 1.125 1.125 1.125M19.125 12h1.5m0 0c.621 0 1.125.504 1.125 1.125v1.5c0 .621-.504 1.125-1.125 1.125m-17.25 0h1.5m14.25 0h1.5"
                  />
                </svg>
              </a>
            ) : null}
          </div>
        )}
        {imageIsLoaded && label && (
          <div className="p-1 rounded-md bg-stone-50 border absolute right-1 top-1 block text-lime-700 opacity-90">
            {label}
          </div>
        )}
        {objectUrl !== null ? (
          href ? (
            <Link href={href} target={target}>
              <img
                src={objectUrl}
                className="rounded-md"
                onLoad={() => setImageIsLoaded(true)}
              />
            </Link>
          ) : (
            <img
              src={objectUrl}
              className="rounded-md"
              onLoad={() => setImageIsLoaded(true)}
              onError={() => setImageIsLoaded(false)}
            />
          )
        ) : !loading ? (
          // This one frame didn't sign — keep the pager so the rest stay reachable.
          <div className="px-4 py-6 text-sm text-stone-500 italic">
            {frameLabel(frames[frameIndex], frameIndex)} could not be loaded.
          </div>
        ) : null}
      </div>

      {showNav && (
        <div className="mt-2 flex items-center justify-center gap-4 select-none">
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
          <span className="text-sm tabular-nums text-stone-500">
            {frameLabel(frames[frameIndex], frameIndex)}
            <span className="ml-2 text-stone-400">
              {frameIndex + 1} / {total}
            </span>
          </span>
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
        </div>
      )}

      {!thumb && shortfall && (
        <p className="mt-2 text-center text-sm text-stone-500 italic">
          {shortfall}
        </p>
      )}
    </div>
  );
}
