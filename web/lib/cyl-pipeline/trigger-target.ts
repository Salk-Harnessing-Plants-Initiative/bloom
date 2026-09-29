/**
 * What a run action targets, as the confirm dialog enumerates it and posts it
 * to POST /api/cyl/pipeline. The proxy adds `params: {}` itself (design D1),
 * so the client never sends params.
 */

export type TriggerTarget =
  | { target_level: "scan" | "wave" | "experiment"; target_id: number }
  | { target_level: "scan_ids"; scan_ids: number[] };
