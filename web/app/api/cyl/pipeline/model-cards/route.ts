/**
 * Server-side proxy for the production model cards (bloom#971), read by the
 * pipeline confirm dialog to warn about scans past their models' validated age
 * or with no model.
 *
 * Upstream is the workflows service's `GET /model-cards`. Like the trigger
 * proxy, this is not a security boundary: Caddy also publishes the service at
 * `/workflows/model-cards`, and the token check lives upstream.
 *
 * Checks, in order: starting runs is switched on (503), then a session with an
 * access token (401). There is no Origin check: browsers send no `Origin` on a
 * same-origin GET, and this route only reads non-sensitive data.
 */

import { getSession } from "@/lib/supabase/server";
import { isPipelineTriggerEnabled } from "@/lib/cyl-pipeline/trigger-enabled";
import {
  MODEL_CARDS_NOT_ENABLED,
  MODEL_CARDS_SIGNED_OUT,
  forwardToModelCards,
  modelCardsDetail,
} from "@/lib/cyl-pipeline/model-cards-proxy";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

export async function GET(_request: Request): Promise<Response> {
  if (!isPipelineTriggerEnabled()) return modelCardsDetail(503, MODEL_CARDS_NOT_ENABLED);
  const session = await getSession();
  if (!session?.access_token) return modelCardsDetail(401, MODEL_CARDS_SIGNED_OUT);
  // Read per request, not at module load, so one image works in any environment.
  const workflowsUrl = process.env.WORKFLOWS_URL ?? "http://workflows:5100";
  return forwardToModelCards(workflowsUrl, session.access_token);
}
