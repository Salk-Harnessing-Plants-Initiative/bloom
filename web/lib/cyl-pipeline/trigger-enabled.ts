/**
 * Whether members may start cylinder pipeline runs from Bloom. Read on the
 * server at request time from CYL_PIPELINE_TRIGGER_ENABLED, never baked into
 * the client bundle, so an environment can be switched without a rebuild. On
 * only for exactly "true".
 *
 * It is off in prod until bloom#863 is fixed: every dispatched Workflow mounts
 * the staging Supabase credential, so a prod run's write-back would land in
 * staging and its own rows would end up failed. Off hides every run action and
 * makes POST /api/cyl/pipeline answer 503. The live views stay. It doesn't
 * reach the Workflows service, whose own POST /pipeline stays public
 * (bloom#983).
 */

export const TRIGGER_ENABLED_ENV = "CYL_PIPELINE_TRIGGER_ENABLED";

export function isPipelineTriggerEnabled(env: Record<string, string | undefined> = process.env): boolean {
  return env[TRIGGER_ENABLED_ENV] === "true";
}
