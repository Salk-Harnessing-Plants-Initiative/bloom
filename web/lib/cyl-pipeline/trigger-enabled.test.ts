/** The server-side switch for starting pipeline runs from Bloom (PR 6 review, bloom#863). */

import { describe, expect, it } from "vitest";
import { isPipelineTriggerEnabled, TRIGGER_ENABLED_ENV } from "./trigger-enabled";

describe("isPipelineTriggerEnabled", () => {
  it("reads CYL_PIPELINE_TRIGGER_ENABLED", () => {
    expect(TRIGGER_ENABLED_ENV).toBe("CYL_PIPELINE_TRIGGER_ENABLED");
  });

  it("is on only for exactly true", () => {
    expect(isPipelineTriggerEnabled({ CYL_PIPELINE_TRIGGER_ENABLED: "true" })).toBe(true);
  });

  it.each([[undefined], [""], ["false"], ["TRUE"], ["1"], ["yes"], [" true"]])("is off for %j", (value) => {
    expect(isPipelineTriggerEnabled({ CYL_PIPELINE_TRIGGER_ENABLED: value })).toBe(false);
  });

  it("is off when the variable is absent", () => {
    expect(isPipelineTriggerEnabled({})).toBe(false);
  });
});
