/**
 * Middleware must not log user identity (issue #108 item 8).
 *
 * `middleware.ts` runs on every matched request, and its output goes to the
 * container's stdout — so anything logged there is retained for as long as
 * the logs are, for every request, for every user. A `console.log` of
 * `user.email` therefore accumulates an attributable record of who used the
 * application and when, which is not something the logs are meant to hold.
 *
 * Source-shape rather than behavioural: the regression this guards is a
 * debug line being added back during troubleshooting and left in, which is
 * exactly what happened before — every other debug statement in this file
 * was commented out and this one was missed. Reading the source catches that
 * at the point it is introduced, without needing to drive the middleware.
 */

import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

const SOURCE = readFileSync(join(__dirname, "middleware.ts"), "utf8");

/** Lines that actually execute — comment lines are debug history, not output. */
function activeLines(): string[] {
  return SOURCE.split("\n").filter((line) => !line.trim().startsWith("//"));
}

/** Console calls that would reach stdout on a real request. */
function consoleCalls(): string[] {
  return activeLines().filter((line) => /console\.(log|info|warn|error|debug)\(/.test(line));
}

describe("middleware logging", () => {
  it("logs no user identity", () => {
    // `user?.email`, `user.id`, session objects — anything that ties a log
    // line to a person. `error.message` is fine: it describes a failure, not
    // who hit it.
    const identity = /\b(email|user\b|session|access_?[Tt]oken|jwt)/;
    const offenders = consoleCalls().filter((line) => identity.test(line));

    expect(
      offenders,
      "middleware runs on every request — a console call naming the user " +
        "writes an attributable record to container logs continuously",
    ).toEqual([]);
  });

  it("logs no bearer tokens or keys", () => {
    const secret = /\b(anonKey|serviceRole|apikey|Authorization|Bearer)\b/i;
    const offenders = consoleCalls().filter((line) => secret.test(line));

    expect(offenders, "credentials must never reach stdout").toEqual([]);
  });
});
