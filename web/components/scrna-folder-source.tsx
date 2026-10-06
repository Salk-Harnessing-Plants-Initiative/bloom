"use client";

import { useEffect, useId, useRef, useState } from "react";
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
// changing, and again whenever recheck changes or "Check again" is pressed. onChecked gets
// the passed check, or null while there isn't one for this URL.
export default function ScrnaFolderSource({
  url,
  recheck = 0,
  readerArn = null,
  onUrl,
  onChecked,
  fieldClass,
  labelClass,
}: {
  url: string;
  recheck?: number;
  // Bloom's read-only AWS user; with it, the help offers sharing a private folder.
  readerArn?: string | null;
  onUrl: (url: string) => void;
  onChecked: (check: FolderCheck | null) => void;
  fieldClass: string;
  labelClass: string;
}) {
  const [state, setState] = useState<CheckState>({ kind: "idle" });
  const [showLayout, setShowLayout] = useState(false);
  const [retry, setRetry] = useState(0);
  // The URL once typing has paused, or the field was left; a half-typed URL isn't an error.
  const [settledUrl, setSettledUrl] = useState(url);
  const statusId = useId();
  const layoutId = useId();
  const target = normaliseFolderUrl(url);
  const problem = settledUrl === url ? folderUrlProblem(url) : null;
  // The latest callback, so a parent passing a new one each render doesn't restart the check.
  const onCheckedRef = useRef(onChecked);
  useEffect(() => {
    onCheckedRef.current = onChecked;
  }, [onChecked]);

  useEffect(() => {
    const timer = setTimeout(() => setSettledUrl(url), FOLDER_CHECK_DELAY_MS);
    return () => clearTimeout(timer);
  }, [url]);

  // Keyed on the normalised URL, so adding the trailing "/" doesn't check the same folder again.
  useEffect(() => {
    onCheckedRef.current(null);
    if (!looksLikeFolder(target)) {
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
          body: JSON.stringify({ fastq_url: target }),
          signal: controller.signal,
        });
        const body: unknown = await response.json().catch(() => null);
        if (controller.signal.aborted) return;
        if (response.ok && isFolderCheck(body)) {
          setState({ kind: "ok", check: body });
          onCheckedRef.current(body);
        } else {
          setState({
            kind: "error",
            message: response.ok
              ? "The job service returned an unexpected response."
              : folderCheckErrorMessage(
                  response.status,
                  (body as { detail?: unknown } | null)?.detail
                ),
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
  }, [target, recheck, retry]);

  return (
    <div className="space-y-2">
      <div className="flex items-end gap-2">
        <label className={`${labelClass} flex-1`}>
          S3 folder URL
          <input
            className={`${fieldClass} font-mono`}
            value={url}
            onChange={(e) => onUrl(e.target.value)}
            onBlur={() => setSettledUrl(url)}
            onKeyDown={(e) => {
              // Enter here would submit the whole form; the folder is checked on its own.
              if (e.key === "Enter") {
                e.preventDefault();
                setSettledUrl(url);
              }
            }}
            placeholder={FOLDER_EXAMPLE.url}
            spellCheck={false}
            autoComplete="off"
            aria-describedby={statusId}
            aria-invalid={Boolean(problem) || state.kind === "error"}
          />
        </label>
        <button
          type="button"
          onClick={() => setShowLayout((v) => !v)}
          aria-expanded={showLayout}
          aria-controls={layoutId}
          aria-label="What the folder must contain"
          className="mb-1 flex h-8 w-8 shrink-0 items-center justify-center rounded-full border border-stone-300 bg-white text-sm font-medium text-stone-600 hover:border-lime-700 hover:text-lime-800"
        >
          ?
        </button>
      </div>

      {showLayout ? (
        <div
          id={layoutId}
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
          </ul>
          <p className="mt-2 font-medium text-stone-700">Giving Bloom the reads</p>
          <ul className="mt-1 list-disc space-y-1 pl-5">
            <li>
              A public folder: upload the reads to a public bucket, such as{" "}
              <code>salk-tm-pub</code>, or a bucket readable from the Salk network, and paste
              the path. A public bucket is open to anyone on the internet.
            </li>
            {readerArn ? (
              <li>
                A private folder: give Bloom&apos;s reader permission to list and read the
                folder, then paste the path. Bloom&apos;s reader is{" "}
                <code className="[overflow-wrap:anywhere]">{readerArn}</code>. Remove the
                permission when the run is done; Bloom emails you when it finishes.
              </li>
            ) : null}
          </ul>
          <p className="mt-2 font-medium text-stone-700">For example</p>
          <pre className="mt-1 overflow-x-auto rounded bg-stone-50 p-2 text-xs">
            {[FOLDER_EXAMPLE.url, ...FOLDER_EXAMPLE.files.map((f) => `    ${f}`)].join("\n")}
          </pre>
        </div>
      ) : null}

      {/* One polite region: a refusal is read when it arrives, without cutting off typing. */}
      <div id={statusId} aria-live="polite" className="text-sm [overflow-wrap:anywhere]">
        {problem ? (
          <p className="text-red-700">{problem}</p>
        ) : state.kind === "checking" ? (
          <div className="space-y-1.5 pt-1">
            {/* No fraction to report while S3 answers, so a segment sweeps across. */}
            <div
              role="progressbar"
              aria-label="Checking the folder"
              className="h-1.5 w-full overflow-hidden rounded-full bg-stone-200"
            >
              <div className="h-1.5 w-1/4 animate-sweep rounded-full bg-lime-400 shadow-[0_0_8px_2px_rgba(163,230,53,0.7)] motion-reduce:animate-pulse" />
            </div>
            <p className="text-stone-500">
              Verifying the S3 folder is accessible and correctly formatted…
            </p>
          </div>
        ) : state.kind === "ok" ? (
          <p className="text-lime-800">{folderSummary(state.check)}</p>
        ) : state.kind === "error" ? (
          <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
            <p className="text-red-700">{state.message}</p>
            <button
              type="button"
              onClick={() => setRetry((n) => n + 1)}
              className="text-sm font-medium text-lime-800 underline hover:text-lime-900"
            >
              Check again
            </button>
          </div>
        ) : (
          <p className="text-stone-500">
            The folder is checked as soon as you enter it.
          </p>
        )}
      </div>
    </div>
  );
}
