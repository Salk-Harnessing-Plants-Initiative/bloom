// @vitest-environment jsdom
/**
 * LiveIndicator's optional connection state (add-cyl-pipeline-ui task 5.1).
 * With no props it must render exactly as it did before, since both
 * recent-phenotype widgets use it that way.
 */

import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { LiveIndicator } from "./LiveIndicator";

afterEach(cleanup);

// The markup before the state prop existed, inlined as the expected value.
const TODAY =
  '<span class="inline-flex items-center gap-1.5 text-xs font-medium text-stone-600">' +
  '<span class="relative inline-flex h-2 w-2">' +
  '<span class="absolute inline-flex h-full w-full animate-ping rounded-full bg-green-400 opacity-75"></span>' +
  '<span class="relative inline-flex h-2 w-2 rounded-full bg-green-600"></span>' +
  "</span>Live</span>";

describe("LiveIndicator", () => {
  it("(characterization) renders as today with no props", () => {
    const { container } = render(<LiveIndicator />);
    expect(container.innerHTML).toBe(TODAY);
  });

  it("(characterization) is unchanged for the plate-scanner widget, which passes no props", async () => {
    // jsdom's import.meta.url isn't a file: URL; vitest runs from web/.
    const { readFileSync } = await import("node:fs");
    const { join } = await import("node:path");
    const source = readFileSync(
      join(process.cwd(), "components/recent-phenotypes-by-plate-scanner/RecentPhenotypesByPlateScanner.tsx"),
      "utf8",
    );
    expect(source).toMatch(/<LiveIndicator \/>/);
  });

  it("renders live like today, announced politely", () => {
    render(<LiveIndicator state="live" />);
    const status = screen.getByRole("status");
    expect(status.getAttribute("aria-live")).toBe("polite");
    expect(status.textContent).toBe("Live");
  });

  it("gives connecting and offline their own announced text", () => {
    const { unmount } = render(<LiveIndicator state="connecting" />);
    const connecting = screen.getByRole("status").textContent;
    expect(connecting).toMatch(/connecting/i);
    unmount();

    render(<LiveIndicator state="offline" onRefresh={() => {}} />);
    const status = screen.getByRole("status");
    expect(status.getAttribute("aria-live")).toBe("polite");
    expect(status.textContent).toMatch(/offline/i);
    expect(status.textContent).not.toBe(connecting);
  });

  it("offers a refresh control when offline, and calls onRefresh", () => {
    const onRefresh = vi.fn();
    render(<LiveIndicator state="offline" onRefresh={onRefresh} />);
    fireEvent.click(screen.getByRole("button", { name: /refresh/i }));
    expect(onRefresh).toHaveBeenCalledTimes(1);
  });

  it("offers no refresh control while live or connecting", () => {
    render(<LiveIndicator state="live" onRefresh={() => {}} />);
    expect(screen.queryByRole("button")).toBeNull();
  });
});
