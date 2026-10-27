/**
 * The confirm dialog's read of the production model cards (bloom#971), through
 * GET /api/cyl/pipeline/model-cards. Client-safe: the dialog imports it; the
 * server-only proxy helpers live in model-cards-proxy.ts.
 *
 * The read is optional. It returns the cards, or null for any failure, and
 * never throws, so the dialog can show "Couldn't check the models' age ranges."
 * instead of failing. Its timeout uses AbortController + setTimeout rather than
 * AbortSignal.timeout so tests can drive it with fake timers, and it also
 * covers reading the body.
 */

import type { ModelCardEntry } from "./model-windows";

export const MODEL_CARDS_URL = "/api/cyl/pipeline/model-cards";
export const MODEL_CARDS_TIMEOUT_MS = 10_000;

const isRecord = (v: unknown): v is Record<string, unknown> => typeof v === "object" && v !== null;
const isInt = (v: unknown): v is number => typeof v === "number" && Number.isInteger(v);

function isSelector(v: unknown): boolean {
  return (
    isRecord(v) &&
    typeof v.species === "string" &&
    typeof v.mode === "string" &&
    isInt(v.age_min) &&
    isInt(v.age_max)
  );
}

function isCard(v: unknown): boolean {
  return (
    isRecord(v) &&
    typeof v.root_type === "string" &&
    typeof v.registry_id === "string" &&
    typeof v.version === "string" &&
    Array.isArray(v.selectors) &&
    v.selectors.every(isSelector)
  );
}

/** The body of a successful GET /model-cards. */
export function isCardList(v: unknown): v is { cards: ModelCardEntry[]; fetched_at: string; skipped: number } {
  return (
    isRecord(v) &&
    typeof v.fetched_at === "string" &&
    isInt(v.skipped) &&
    v.skipped >= 0 &&
    Array.isArray(v.cards) &&
    v.cards.every(isCard)
  );
}

/** The production cards, and how many production cards the service couldn't read. */
export interface ModelCardsRead {
  cards: ModelCardEntry[];
  skipped: number;
}

export async function fetchModelCards(): Promise<ModelCardsRead | null> {
  const controller = new AbortController();
  const aborted = new Promise<never>((_resolve, reject) => {
    controller.signal.addEventListener("abort", () => reject(controller.signal.reason));
  });
  aborted.catch(() => {});
  const timer = setTimeout(
    () => controller.abort(new DOMException("model cards timed out", "TimeoutError")),
    MODEL_CARDS_TIMEOUT_MS
  );
  try {
    const res = await Promise.race([fetch(MODEL_CARDS_URL, { signal: controller.signal }), aborted]);
    if (!res.ok) return null;
    const body: unknown = await Promise.race([res.json(), aborted]);
    return isCardList(body) ? { cards: body.cards, skipped: body.skipped } : null;
  } catch {
    return null;
  } finally {
    clearTimeout(timer);
  }
}
