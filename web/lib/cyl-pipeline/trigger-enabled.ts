/**
 * Whether members may start cylinder pipeline runs from Bloom. Read on the
 * server at request time from CYL_PIPELINE_TRIGGER_ENABLED, never baked into
 * the client bundle, so an environment can be switched without a rebuild. On
 * only for exactly "true".
 *
 * It is on in staging and prod. Prod was off until its own pipeline
 * credential Secret and stage directories were provisioned (bloom#863).
 * Off hides every run action and makes POST /api/cyl/pipeline answer 503.
 * The live views stay. The Workflows service's dispatch worker reads the same
 * switch (at start-up), and while it is off fails every batch it claims: a
 * direct POST /workflows/pipeline still creates a run, but none of its batches
 * reaches the cluster.
 */

export const TRIGGER_ENABLED_ENV = "CYL_PIPELINE_TRIGGER_ENABLED";

/** What a pipeline proxy answers (503) while starting runs is switched off. */
export const TRIGGER_DISABLED_DETAIL = "Starting pipeline runs from Bloom is not enabled in this environment.";

export function isPipelineTriggerEnabled(env: Record<string, string | undefined> = process.env): boolean {
  return env[TRIGGER_ENABLED_ENV] === "true";
}
