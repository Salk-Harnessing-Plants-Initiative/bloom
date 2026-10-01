// @vitest-environment jsdom
/** The drill-down page: invalid and unknown ids are not found; a failed lookup is an error, not a 404. */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { mockClient, queriesFor, resetSupabaseMock, supabaseMock } from "@/lib/cyl-pipeline/__fixtures__/supabase-mock";
import { at, runRow } from "@/lib/cyl-pipeline/__fixtures__/rows";

const detail = vi.hoisted(() => ({ props: null as Record<string, unknown> | null }));

vi.mock("next/navigation", () => ({
  notFound: () => {
    throw new Error("NEXT_NOT_FOUND");
  },
}));
vi.mock("@/lib/supabase/server", () => ({ createServerSupabaseClient: async () => mockClient }));
vi.mock("./RunDetailLive", () => ({
  RunDetailLive: (props: Record<string, unknown>) => {
    detail.props = props;
    return <div data-testid="detail" />;
  },
}));

import RunPage from "./page";

const page = (runId: string, searchParams: Record<string, string | string[] | undefined> = {}) =>
  RunPage({ params: Promise.resolve({ runId }), searchParams: Promise.resolve(searchParams) });

beforeEach(() => {
  resetSupabaseMock();
  detail.props = null;
});
afterEach(() => {
  cleanup();
  vi.unstubAllEnvs();
});

describe("the run drill-down page", () => {
  it.each(["abc", "0", "-1", "1.5", "0x10", "1e2", "9007199254740993", ""])(
    "calls notFound() for the invalid id %j without querying",
    async (runId) => {
      await expect(page(runId)).rejects.toThrow("NEXT_NOT_FOUND");
      expect(supabaseMock.queries).toHaveLength(0);
    },
  );

  it("calls notFound() for a valid id with no visible run", async () => {
    supabaseMock.respond = () => ({ data: null, error: null });
    await expect(page("91")).rejects.toThrow("NEXT_NOT_FOUND");
    const [q] = queriesFor("cyl_pipeline_runs");
    expect(q.arg("eq")).toEqual(["id", 91]);
    expect(q.arg("maybeSingle")).toEqual([]);
  });

  it("renders an error, not the not-found page, when the lookup fails", async () => {
    supabaseMock.respond = () => ({ data: null, error: { message: "connection refused" } });
    render(await page("91"));
    expect(screen.getByRole("alert").textContent).toContain("connection refused");
    expect(screen.queryByTestId("detail")).toBeNull();
  });

  it("renders the run", async () => {
    const run = runRow(91, at(1));
    supabaseMock.respond = () => ({ data: run, error: null });
    render(await page("91"));
    expect(screen.getByRole("heading", { name: "Run 91" })).toBeTruthy();
    expect(screen.getByRole("link", { name: "All cylinder pipeline runs" }).getAttribute("href")).toBe("/app/cyl-pipeline-runs");
    expect(detail.props).toMatchObject({ initialRun: run, initialFilter: "all" });
  });

  it("opens the table filtered by a valid status param, and ignores others", async () => {
    supabaseMock.respond = () => ({ data: runRow(91, at(1)), error: null });
    render(await page("91", { status: "failed" }));
    expect(detail.props?.initialFilter).toBe("failed");
    cleanup();
    render(await page("91", { status: ["failed", "waiting"] }));
    expect(detail.props?.initialFilter).toBe("all");
    cleanup();
    render(await page("91", { status: "bogus" }));
    expect(detail.props?.initialFilter).toBe("all");
  });

  it("tells the drill-down whether starting runs is switched on", async () => {
    vi.stubEnv("CYL_PIPELINE_TRIGGER_ENABLED", "true");
    render(await page("91"));
    expect(detail.props?.triggerEnabled).toBe(true);
    cleanup();
    vi.stubEnv("CYL_PIPELINE_TRIGGER_ENABLED", "false");
    render(await page("91"));
    expect(detail.props?.triggerEnabled).toBe(false);
  });
});
