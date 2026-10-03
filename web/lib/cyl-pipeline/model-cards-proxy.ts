/**
 * The upstream call and response mapping behind GET /api/cyl/pipeline/model-cards
 * (bloom#971). Server-only; route.ts orders the checks. Upstream is the workflows
 * service's GET /model-cards, which reads the production model cards from the
 * wandb registry (cached there).
 *
 * Only a well-formed card list passes through. Every other answer becomes a
 * fixed 502 (504 for this route's own timeout) that never echoes upstream text;
 * the dialog shows "Couldn't check the models' age ranges." for any of them.
 */

import { NextResponse } from "next/server";
import { isCardList } from "./model-cards";
import { detailResponse } from "./trigger-proxy";

// Under the dialog's own 10 s budget, so the dialog sees this route's answer.
export const MODEL_CARDS_UPSTREAM_TIMEOUT_MS = 8_000;

export const MODEL_CARDS_SIGNED_OUT = "Sign in to check the model cards.";
export const MODEL_CARDS_UNAVAILABLE = "Couldn't read the model catalog.";
export const MODEL_CARDS_TIMED_OUT = "Reading the model catalog timed out.";

const LOG_PREFIX = "[api/cyl/pipeline/model-cards]";

export const modelCardsDetail = detailResponse;

export async function forwardToModelCards(workflowsUrl: string, accessToken: string): Promise<NextResponse> {
  let upstream: Response;
  let text: string;
  try {
    upstream = await fetch(`${workflowsUrl}/model-cards`, {
      headers: { Authorization: `Bearer ${accessToken}` },
      // A redirect is not an answer this route knows; it maps to 502.
      redirect: "manual",
      signal: AbortSignal.timeout(MODEL_CARDS_UPSTREAM_TIMEOUT_MS),
    });
    text = await upstream.text();
  } catch (err) {
    const name = (err as { name?: unknown } | null)?.name;
    if (name === "TimeoutError") {
      console.error(`${LOG_PREFIX} upstream timed out`);
      return modelCardsDetail(504, MODEL_CARDS_TIMED_OUT);
    }
    console.error(`${LOG_PREFIX} upstream failed: ${String(name ?? "error")}`);
    return modelCardsDetail(502, MODEL_CARDS_UNAVAILABLE);
  }
  if (upstream.status !== 200) {
    console.error(`${LOG_PREFIX} upstream answered ${upstream.status}`);
    return modelCardsDetail(502, MODEL_CARDS_UNAVAILABLE);
  }
  let body: unknown;
  try {
    body = JSON.parse(text);
  } catch {
    console.error(`${LOG_PREFIX} upstream answered 200 with a non-JSON body`);
    return modelCardsDetail(502, MODEL_CARDS_UNAVAILABLE);
  }
  if (!isCardList(body)) {
    console.error(`${LOG_PREFIX} upstream answered 200 with an unexpected shape`);
    return modelCardsDetail(502, MODEL_CARDS_UNAVAILABLE);
  }
  // Rebuilt from the checked fields, so nothing else upstream sent reaches the browser.
  return NextResponse.json({ cards: body.cards, fetched_at: body.fetched_at, skipped: body.skipped }, { status: 200 });
}
