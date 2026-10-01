/**
 * Local validation for POST /api/cyl/pipeline, and the body it forwards.
 *
 * The rules are the spec's. They follow the trigger's own `_validate_request`
 * in services/workflows/pipeline.py, which stays authoritative for what reaches
 * it, but they are not identical:
 *  - stricter: ids must be safe integers, and `scan_ids: null` is refused
 *    unless the level is `scan_ids`;
 *  - looser: client `params` are ignored, not validated, since they are never
 *    forwarded; and JSON numbers such as `42.0` or `1e2` are integers once
 *    parsed here, so they pass and are forwarded as `42` and `100`, where the
 *    trigger would refuse the float if called directly. What is forwarded is
 * rebuilt from the validated fields, never spread from the client's object, and
 * `params` is always `{}`, the trigger's true "no overrides" value while
 * overrides are inert (#897).
 */

// Equal to MAX_SCAN_IDS in services/workflows/pipeline.py; trigger-request.test.ts
// reads that file and fails if the two drift. Applies to `scan_ids` targets only.
export const MAX_TRIGGER_SCAN_IDS = 5000;

/** Why a `scan_ids` action over the limit can't be sent, as its button and the confirm dialog say it. */
export function scanIdsOverLimitText(count: number): string {
  return `This covers ${count} scans; one run of listed scans takes at most ${MAX_TRIGGER_SCAN_IDS}.`;
}

type EmptyParams = Record<string, never>;

export type TriggerRequest =
  | { target_level: "scan" | "wave" | "experiment"; target_id: number; params: EmptyParams }
  | { target_level: "scan_ids"; scan_ids: number[]; params: EmptyParams };

export type ParsedTriggerRequest =
  | { ok: true; body: TriggerRequest }
  | { ok: false; detail: string };

const SINGLE_TARGET_LEVELS = new Set(["scan", "wave", "experiment"]);

function isSafePositiveInteger(value: unknown): value is number {
  return typeof value === "number" && Number.isSafeInteger(value) && value > 0;
}

function refuse(detail: string): ParsedTriggerRequest {
  return { ok: false, detail };
}

export function parseTriggerRequest(raw: unknown): ParsedTriggerRequest {
  if (typeof raw !== "object" || raw === null || Array.isArray(raw)) {
    return refuse("The request body must be a JSON object.");
  }
  const { target_level, target_id, scan_ids } = raw as Record<string, unknown>;

  if (target_level === "scan_ids") {
    if (target_id !== undefined && target_id !== null) {
      return refuse("target_id must be absent or null when target_level is scan_ids.");
    }
    if (!Array.isArray(scan_ids) || scan_ids.length === 0) {
      return refuse("scan_ids must be a non-empty array when target_level is scan_ids.");
    }
    if (scan_ids.length > MAX_TRIGGER_SCAN_IDS) {
      return refuse(`scan_ids must contain at most ${MAX_TRIGGER_SCAN_IDS} entries.`);
    }
    if (!scan_ids.every(isSafePositiveInteger)) {
      return refuse("scan_ids entries must be positive integers.");
    }
    return { ok: true, body: { target_level, scan_ids: [...scan_ids], params: {} } };
  }

  if (typeof target_level !== "string" || !SINGLE_TARGET_LEVELS.has(target_level)) {
    return refuse("target_level must be one of scan, wave, experiment, scan_ids.");
  }
  if (scan_ids !== undefined) {
    return refuse("scan_ids must be absent unless target_level is scan_ids.");
  }
  if (!isSafePositiveInteger(target_id)) {
    return refuse("target_id must be a positive integer.");
  }
  return {
    ok: true,
    body: {
      target_level: target_level as "scan" | "wave" | "experiment",
      target_id,
      params: {},
    },
  };
}

export interface TriggerResult {
  pipeline_run_id: number;
  scan_count: number;
}

/**
 * A well-formed trigger answer: `pipeline_run_id` and `scan_count` are safe
 * integers. The proxy passes only these through as a success, and the confirm
 * dialog treats anything else as "may have started".
 */
export function isTriggerResult(parsed: unknown): parsed is TriggerResult {
  if (typeof parsed !== "object" || parsed === null) return false;
  const { pipeline_run_id, scan_count } = parsed as Record<string, unknown>;
  return Number.isSafeInteger(pipeline_run_id) && Number.isSafeInteger(scan_count);
}
