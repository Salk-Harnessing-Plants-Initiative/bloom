import { describe, expect, it, vi } from "vitest";

vi.mock("next/navigation", () => ({
  permanentRedirect: (to: string) => {
    throw new Error(`redirect ${to}`);
  },
}));
vi.mock("@/lib/supabase/server", () => ({ getUser: async () => null }));
vi.mock("mixpanel", () => ({ default: { init: vi.fn() } }));
vi.mock("./panels/CylinderPanel", () => ({ default: async () => null }));
vi.mock("./panels/PlatePanel", () => ({ default: async () => null }));
vi.mock("./TimelineHub", () => ({ default: () => null }));

import Timeline from "./page";

describe("the Timeline page", () => {
  it.each([["rnaseq"], [["rnaseq", "plate"]]])(
    "sends the former RNA-seq panel (?panel=%j) to the RNA-seq runs page",
    async (panel) => {
      await expect(Timeline({ searchParams: Promise.resolve({ panel }) })).rejects.toThrow(
        "redirect /app/rnaseq-runs",
      );
    },
  );

  it.each([[undefined], ["plate"], ["unknown"]])("shows a scanner panel for ?panel=%j", async (panel) => {
    await expect(Timeline({ searchParams: Promise.resolve({ panel }) })).resolves.toBeTruthy();
  });
});
