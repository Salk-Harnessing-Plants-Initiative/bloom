import { describe, expect, it, vi } from "vitest";

vi.mock("next/navigation", () => ({
  notFound: () => {
    throw new Error("not found");
  },
  permanentRedirect: (to: string) => {
    throw new Error(`redirect ${to}`);
  },
}));

import FormerRnaseqRunPage from "./page";

describe("a run's former address, as in e-mails already sent", () => {
  it("goes to the run's own page", async () => {
    await expect(FormerRnaseqRunPage({ params: Promise.resolve({ runId: "42" }) })).rejects.toThrow(
      "redirect /app/rnaseq-runs/42",
    );
  });

  it("is not found for a run id that isn't one", async () => {
    await expect(FormerRnaseqRunPage({ params: Promise.resolve({ runId: "x" }) })).rejects.toThrow("not found");
  });
});
