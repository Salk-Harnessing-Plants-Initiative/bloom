"use client";

/** One row of the Video Jobs table: Video, Requested, Progress. */

import Link from "next/link";
import { formatElapsed } from "@/lib/cyl-pipeline/elapsed";
import { requesterText } from "@/lib/cyl-pipeline/run-text";
import {
  PLATE_FAILURES,
  SCAN_FAILURES,
  countedFrames,
  jobFailureMessage,
  queuedNote,
  renderingNote,
  type VideoJob,
  type VideoJobStatus,
} from "@/lib/video-job";
import { overdue, type ListedJob, type PlatePlace, type ScanPlace } from "@/lib/video-jobs/list";

const DONE: Record<string, string> = {
  rendered: "Video ready",
  kept: "Already up to date — the stored video was kept",
};

function asVideoJob(job: ListedJob, ahead: number | null): VideoJob {
  return {
    id: job.id,
    status: job.status as VideoJobStatus,
    stage: job.stage,
    frames_done: job.frames_done,
    frames_total: job.frames_total,
    error_code: job.error_code,
    queued_ahead: ahead,
    created_at: job.created_at,
    updated_at: job.updated_at,
  };
}

function what(job: ListedJob, plate: PlatePlace | undefined, scan: ScanPlace | undefined) {
  if (job.kind === "plate") {
    const wave = job.wave_number === null ? "no wave" : `wave ${job.wave_number}`;
    return {
      label: "Plate time-lapse",
      title: `${job.plate_id} · ${wave}`,
      experiment: plate?.name ?? `Experiment ${job.experiment_id}`,
    };
  }
  const day = scan?.plant_age_days == null ? "" : ` · day ${scan.plant_age_days}`;
  return {
    label: "Cylinder scan",
    title: `Scan ${job.scan_id}${day}`,
    experiment: scan?.experiment_name ?? `Experiment ${job.experiment_id}`,
  };
}

export function VideoJobRow({
  job,
  ahead,
  href,
  plate,
  scan,
  currentUserId,
  now,
  highlighted,
}: {
  job: ListedJob;
  ahead: number | null;
  href: string | null;
  plate: PlatePlace | undefined;
  scan: ScanPlace | undefined;
  currentUserId: string | null;
  now: number | null;
  highlighted: boolean;
}) {
  const video = asVideoJob(job, ahead);
  const { label, title, experiment } = what(job, plate, scan);
  const counted = countedFrames(video);
  const slow = now === null ? null : overdue(job, now);

  return (
    <tr
      id={`job-${job.id}`}
      data-testid={`job-${job.id}`}
      aria-current={highlighted ? "true" : undefined}
      className={"border-t border-stone-100 align-top" + (highlighted ? " bg-lime-50" : "")}
    >
      <td className="px-3 py-3">
        <div className="text-xs uppercase tracking-wider text-stone-400">{label}</div>
        {href ? (
          <Link href={href} className="font-medium text-lime-700 hover:underline">
            {title}
          </Link>
        ) : (
          <span className="font-medium">{title}</span>
        )}
        <div className="text-xs text-stone-500">{experiment}</div>
      </td>
      <td className="px-3 py-3">
        <div>{requesterText(job.requested_by, currentUserId)}</div>
        <div className="text-xs text-stone-500">
          {now === null ? "" : `${formatElapsed(job.created_at, now)} ago`}
        </div>
      </td>
      <td className="px-3 py-3">
        {job.status === "queued" && <div>{queuedNote(video)}</div>}
        {job.status === "rendering" && (
          <div>
            <div>{renderingNote(video)}</div>
            <div
              role="progressbar"
              aria-label="Rendering"
              aria-valuenow={counted?.done}
              aria-valuemin={counted ? 0 : undefined}
              aria-valuemax={counted?.total}
              className="mt-1 h-1.5 w-48 overflow-hidden rounded-full bg-stone-200"
            >
              {counted ? (
                <div
                  className="h-1.5 rounded-full bg-lime-700 transition-[width] duration-500"
                  style={{ width: `${Math.min(100, (counted.done / counted.total) * 100)}%` }}
                />
              ) : (
                <div className="h-1.5 w-1/4 animate-sweep rounded-full bg-lime-700" />
              )}
            </div>
          </div>
        )}
        {job.status === "failed" && (
          <div className="text-red-700">
            {jobFailureMessage(video, job.kind === "plate" ? PLATE_FAILURES : SCAN_FAILURES)}
          </div>
        )}
        {DONE[job.status] && (
          <div>
            {DONE[job.status]}
            {href && (
              <>
                {" — "}
                <Link href={href} className="text-lime-700 hover:underline">
                  watch it
                </Link>
              </>
            )}
          </div>
        )}
        {slow === "quiet" && (
          <div className="text-xs text-amber-700">
            No update for {formatElapsed(job.updated_at, now!)} — the render may be stuck.
          </div>
        )}
        {slow === "waiting" && (
          <div className="text-xs text-amber-700">Waiting longer than usual.</div>
        )}
      </td>
    </tr>
  );
}
