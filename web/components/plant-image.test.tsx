// @vitest-environment jsdom
/**
 * The signed URL carries a token, so an unnamed image gets the whole query
 * string read out; and an empty src resolves against the document and
 * re-requests the page. Both are attribute-level and regress silently.
 */

import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";

import PlantImage from "./plant-image";

// Signing fails for one path, the way Supabase reports a per-object failure.
vi.mock("@/lib/supabase/client", () => ({
  createClientSupabaseClient: () => ({
    storage: {
      from: () => ({
        createSignedUrl: async (path: string) =>
          path === "unsignable.png"
            ? { data: { signedUrl: "" }, error: null }
            : { data: { signedUrl: `https://signed.test/${path}` }, error: null },
      }),
    },
  }),
}));

afterEach(cleanup);

describe("PlantImage", () => {
  it("names the image rather than leaving the signed URL as its only description", async () => {
    render(<PlantImage path="a.png" thumb />);

    const img = await screen.findByAltText("Plant image");
    expect(img.getAttribute("src")).toBe("https://signed.test/a.png");
  });

  it("renders nothing at all when the object cannot be signed", async () => {
    // An empty src is not "no image" — the browser resolves it against the
    // document and requests the page again.
    const { container } = render(<PlantImage path="unsignable.png" thumb />);

    await waitFor(() =>
      expect(container.querySelector(".animate-pulse")).toBeTruthy()
    );
    expect(container.querySelector("img")).toBeNull();
  });
});
