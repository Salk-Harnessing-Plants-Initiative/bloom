// @vitest-environment jsdom
/** The Species page lists the non-deleted species with who added them, and shows an error when it can't. */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, render, screen, within } from "@testing-library/react";
import { mockClient, queriesFor, resetSupabaseMock, supabaseMock } from "@/lib/cyl-pipeline/__fixtures__/supabase-mock";

vi.mock("@/lib/supabase/client", async () => (await import("@/lib/cyl-pipeline/__fixtures__/supabase-mock")).clientModule);
vi.mock("next/navigation", () => ({ useRouter: () => ({ refresh: vi.fn() }) }));
// Images come from storage and public/species-icons; here, Arabidopsis has one and Rice doesn't.
vi.mock("@/components/illustration", () => ({
  speciesImage: vi.fn(async (path: string | null, name: string) =>
    path ? { url: `signed/${path}`, placeholder: false } : { url: "/species-icons/_default.svg", placeholder: true }
  ),
}));
vi.mock("@/lib/supabase/server", () => ({
  createServerSupabaseClient: async () => mockClient,
  getUser: async () => ({ id: "u-1", email: "scientist@example.org" }),
}));

import SpeciesPage from "./page";

beforeEach(() => resetSupabaseMock());
afterEach(cleanup);

describe("the Species page", () => {
  it("lists the species that haven't been removed, sorted, with who added them", async () => {
    supabaseMock.respond = () => ({
      data: [
        { id: 4, common_name: "Rice", genus: "Oryza", species: "sativa", created_by: "u-1", illustration_path: null },
        { id: 1, common_name: "Arabidopsis", genus: "arabidopsis", species: "Thaliana", created_by: null, illustration_path: "arabidopsis.png" },
      ],
      error: null,
    });
    render(await SpeciesPage());

    expect(screen.getByRole("heading", { name: "Species" })).toBeTruthy();
    expect(screen.getByText("Lists every species in Bloom.")).toBeTruthy();
    expect(screen.getByRole("img", { name: "When to add a species" })).toBeTruthy();
    expect(screen.getByRole("tooltip", { hidden: true }).textContent).toBe(
      "Add a species here before you use it in:• Bloom Desktop, for cylinder scans• an Expression analysis (its form can add one too)"
    );
    const rows = within(screen.getByRole("table", { name: "Species" }))
      .getAllByRole("row")
      .slice(1)
      .map((tr) => within(tr).getAllByRole("cell").slice(0, 2).map((td) => td.textContent));
    // u-1 is the signed-in user (see the getUser mock).
    expect(rows).toEqual([
      ["Arabidopsis (Arabidopsis thaliana)", "Bloom admin"],
      ["Rice (Oryza sativa)", "you"],
    ]);
    const query = queriesFor("species")[0];
    expect(query.arg("select")).toEqual(["id, common_name, genus, species, created_by, illustration_path"]);
    // Each species' own image, or the placeholder.
    const images = [...screen.getByRole("table", { name: "Species" }).querySelectorAll("img")].map((img) =>
      img.getAttribute("src")
    );
    expect(images).toEqual(["signed/arabidopsis.png", "/species-icons/_default.svg"]);
    expect(query.arg("is")).toEqual(["deleted_at", null]);
  });

  it("keeps the server's list with its images once the page is running in the browser", async () => {
    supabaseMock.respond = () => ({
      data: [{ id: 1, common_name: "Arabidopsis", genus: "Arabidopsis", species: "thaliana", created_by: null, illustration_path: "arabidopsis.png" }],
      error: null,
    });
    render(await SpeciesPage());
    // Let any effects and their requests run: before this fix, the browser fetched the
    // species again without their images and replaced the list with placeholders.
    await act(async () => {});
    await act(async () => {});

    expect(queriesFor("species")).toHaveLength(1);
    const images = [...screen.getByRole("table", { name: "Species" }).querySelectorAll("img")].map((img) =>
      img.getAttribute("src")
    );
    expect(images).toEqual(["signed/arabidopsis.png"]);
  });

  it("shows a friendly error, not an empty list, when the species can't be loaded", async () => {
    supabaseMock.respond = () => ({ data: null, error: { message: "connection refused" } });
    const log = vi.spyOn(console, "error").mockImplementation(() => {});
    render(await SpeciesPage());

    expect(screen.getByRole("alert").textContent).toBe("Couldn't load the species. Reload the page to try again.");
    expect(log).toHaveBeenCalledWith("[species] Couldn't load the species:", "connection refused");
    expect(screen.queryByText("No species yet.")).toBeNull();
    expect(screen.queryByRole("button", { name: "Add a species" })).toBeNull();
    log.mockRestore();
  });
});
