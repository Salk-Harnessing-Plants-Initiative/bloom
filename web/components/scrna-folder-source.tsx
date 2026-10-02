"use client";

import { useEffect, useRef, useState } from "react";
import {
  FOLDER_CHECK_DELAY_MS,
  FOLDER_EXAMPLE,
  folderCheckErrorMessage,
  folderSummary,
  folderUrlProblem,
  isFolderCheck,
  looksLikeFolder,
  normaliseFolderUrl,
  type FolderCheck,
} from "@/lib/s3-folder";

type CheckState =
  | { kind: "idle" }
  | { kind: "checking" }
  | { kind: "ok"; check: FolderCheck }
  | { kind: "error"; message: string };

// An S3 folder of one sample's FASTQs, checked by the job service as soon as the URL stops
// changing, and again whenever recheck changes. onChecked gets the passed check, or null
// while there isn't one for this URL.
export default function ScrnaFolderSource({
  url,
  recheck = 0,
  onUrl,
  onChecked,
  fieldClass,
  labelClass,
}: {
  url: string;
  recheck?: number;
  onUrl: (url: string) => void;
  onChecked: (check: FolderCheck | null) => void;
  fieldClass: string;
  labelClass: string;
}) {
  const [state, setState] = useState<CheckState>({ kind: "idle" });
  const [showLayout, setShowLayout] = useState(false);
  const problem = folderUrlProblem(url);
  // The latest callback, so a parent passing a new one each render doesn't restart the check.
  const onCheckedRef = useRef(onChecked);
  useEffect(() => {
    onCheckedRef.current = onChecked;
  }, [onChecked]);

  useEffect(() => {
    onCheckedRef.current(null);
    if (!looksLikeFolder(url)) {
      setState({ kind: "idle" });
      return;
    }
    const controller = new AbortController();
    setState({ kind: "checking" });
    const timer = setTimeout(async () => {
      try {
        const response = await fetch("/api/scrna/cellranger/folder-check", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ fastq_url: normaliseFolderUrl(url) }),
          signal: controller.signal,
        });
        const body = await response.json().catch(() => null);
        if (controller.signal.aborted) return;
        if (response.ok && isFolderCheck(body)) {
          setState({ kind: "ok", check: body });
          onCheckedRef.current(body);
        } else {
          setState({
            kind: "error",
            message: response.ok
              ? "The job service returned an unexpected response."
              : folderCheckErrorMessage(response.status, body?.detail),
          });
        }
      } catch {
        if (!controller.signal.aborted) {
          setState({ kind: "error", message: "Could not reach the job service." });
        }
      }
    }, FOLDER_CHECK_DELAY_MS);
    // A newer URL replaces this check, and its reply is ignored.
    return () => {
      clearTimeout(timer);
      controller.abort();
    };
  }, [url, recheck]);

  return (
    <div className="space-y-2">
      <div className="flex items-end gap-2">
        <label className={`${labelClass} flex-1`}>
          S3 folder URL
          <input
            className={`${fieldClass} font-mono`}
            value={url}
            onChange={(e) => onUrl(e.target.value)}
            placeholder={FOLDER_EXAMPLE.url}
            spellCheck={false}
            autoComplete="off"
            aria-describedby="s3-folder-status"
          />
        </label>
        <button
          type="button"
          onClick={() => setShowLayout((v) => !v)}
          aria-expanded={showLayout}
          aria-controls="s3-folder-layout"
          aria-label="What folder to give"
          className="mb-1 flex h-8 w-8 shrink-0 items-center justify-center rounded-full border border-stone-300 bg-white text-sm font-medium text-stone-600 hover:border-lime-700 hover:text-lime-800"
        >
          ?
        </button>
      </div>

      {showLayout ? (
        <div
          id="s3-folder-layout"
          className="rounded-md border border-stone-200 bg-white p-3 text-sm text-stone-600"
        >
          <p className="font-medium text-stone-700">One sample&apos;s folder, with:</p>
          <ul className="mt-1 list-disc space-y-1 pl-5">
            <li>
              The FASTQs directly inside it. Subfolders aren&apos;t read, and other files
              (md5sums, reports) are ignored.
            </li>
            <li>
              Files named <code>&lt;sample_name&gt;_S1_L001_R1_001.fastq.gz</code>. Make sure{" "}
              <code>&lt;sample_name&gt;</code> is the same for all reads; it becomes the
              run&apos;s name.
            </li>
            <li>R1 and R2 for every lane; I1 and I2 are optional.</li>
            <li>A public folder, so Bloom has access to the files.</li>
          </ul>
          <p className="mt-2 font-medium text-stone-700">For example</p>
          <pre className="mt-1 overflow-x-auto rounded bg-stone-50 p-2 text-xs">
            {[FOLDER_EXAMPLE.url, ...FOLDER_EXAMPLE.files.map((f) => `    ${f}`)].join("\n")}
          </pre>
        </div>
      ) : null}

      <div id="s3-folder-status" aria-live="polite" className="text-sm">
        {problem ? (
          <p role="alert" className="text-red-700">
            {problem}
          </p>
        ) : state.kind === "checking" ? (
          <p className="flex items-center gap-2 text-stone-500">
            <span
              aria-hidden="true"
              className="h-3 w-3 animate-spin rounded-full border-2 border-stone-300 border-t-lime-700"
            />
            Checking the folder…
          </p>
        ) : state.kind === "ok" ? (
          <p className="text-lime-800">{folderSummary(state.check)}</p>
        ) : state.kind === "error" ? (
          <p role="alert" className="text-red-700">
            {state.message}
          </p>
        ) : (
          <p className="text-stone-500">
            The folder is checked as soon as you enter it.
          </p>
        )}
      </div>
    </div>
  );
}
