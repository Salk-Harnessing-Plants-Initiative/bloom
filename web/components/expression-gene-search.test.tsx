// @vitest-environment jsdom
/** A failed gene search says so, rather than showing an empty list, and a
 *  search replaced by a newer one never speaks over it. */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

/** Either answers at once (failing when `fail` is set), or holds each search
 *  until a test settles it. */
const search = vi.hoisted(() => ({
  fail: null as string | null,
  held: null as Map<string, { resolve: (r: string[]) => void; reject: (e: Error) => void }> | null,
}));

vi.mock("@/components/expression-lib/scrna-client", () => ({
  searchGenes: vi.fn((_datasetId: number, text: string) => {
    if (search.held) {
      return new Promise<string[]>((resolve, reject) => search.held!.set(text, { resolve, reject }));
    }
    return search.fail ? Promise.reject(new Error(search.fail)) : Promise.resolve(["AT1G01010"]);
  }),
}));

import { ExpressionGeneSearch } from "./expression-gene-search";

beforeEach(() => {
  search.fail = null;
  search.held = null;
});
afterEach(cleanup);

/** Types into the box as a person would: focused first, since MUI resets an unfocused box. */
const type = (text: string) => {
  const box = screen.getByLabelText("Gene") as HTMLInputElement;
  act(() => box.focus());
  fireEvent.change(box, { target: { value: text } });
};

describe("ExpressionGeneSearch", () => {
  it("says the search failed, and why", async () => {
    search.fail = "statement timeout";
    render(<ExpressionGeneSearch datasetId={1} value={null} onChange={() => {}} />);
    type("AT1");
    expect(await screen.findByText("Could not search genes: statement timeout")).toBeTruthy();
  });

  it("drops the message once a search works again", async () => {
    search.fail = "statement timeout";
    render(<ExpressionGeneSearch datasetId={1} value={null} onChange={() => {}} />);
    type("AT1");
    await screen.findByText(/Could not search genes/);

    search.fail = null;
    type("AT1G");
    await waitFor(() => expect(screen.queryByText(/Could not search genes/)).toBeNull());
  });

  it("ignores a slow search that fails after a newer one worked", async () => {
    search.held = new Map();
    render(<ExpressionGeneSearch datasetId={1} value={null} onChange={() => {}} />);

    type("AT1");
    await waitFor(() => expect(search.held!.has("AT1")).toBe(true));
    type("AT1G");
    await waitFor(() => expect(search.held!.has("AT1G")).toBe(true));

    await act(async () => search.held!.get("AT1G")!.resolve(["AT1G01010"]));
    await act(async () => search.held!.get("AT1")!.reject(new Error("statement timeout")));

    expect(screen.queryByText(/Could not search genes/)).toBeNull();
  });
});
