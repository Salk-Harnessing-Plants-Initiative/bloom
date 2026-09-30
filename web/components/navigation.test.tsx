// @vitest-environment jsdom
/** The sidebar: sub-links under Timeline, and which link is marked as the current page. */

import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";

const nav = vi.hoisted(() => ({ pathname: "/app", search: "" }));
vi.mock("next/navigation", () => ({
  usePathname: () => nav.pathname,
  useSearchParams: () => new URLSearchParams(nav.search),
}));

import { Navigation, isCurrent } from "./navigation";
import { navSections } from "./nav-sections";

afterEach(cleanup);

function current() {
  return screen
    .getAllByRole("link")
    .filter((a) => a.getAttribute("aria-current") === "page")
    .map((a) => a.textContent);
}

function renderAt(pathname: string, search = "") {
  nav.pathname = pathname;
  nav.search = search;
  render(<Navigation sections={navSections} />);
}

describe("isCurrent", () => {
  it.each([
    ["/app/timeline?panel=rnaseq", "/app/timeline", "rnaseq", undefined, true],
    ["/app/timeline?panel=rnaseq", "/app/timeline", "plate", undefined, false],
    ["/app/timeline?panel=cylinder", "/app/timeline", null, "cylinder", true],
    ["/app/timeline?panel=rnaseq", "/app/timeline/rnaseq/12", null, "cylinder", true],
    ["/app/timeline?panel=cylinder", "/app/timeline/rnaseq/12", null, "cylinder", false],
    ["/app/timeline", "/app/timeline", "plate", undefined, true],
    ["/app/traits", "/app/traits/1/2", null, undefined, true],
    ["/app", "/app/traits", null, undefined, false],
  ])("%s on %s (panel %s) is current: %s", (href, pathname, panel, defaultPanel, expected) => {
    expect(isCurrent(href, pathname, panel, defaultPanel)).toBe(expected);
  });
});

describe("Navigation", () => {
  it("lists the Timeline panels under Timeline", () => {
    renderAt("/app");
    const hrefs = screen.getAllByRole("link").map((a) => a.getAttribute("href"));
    const at = hrefs.indexOf("/app/timeline");
    expect(hrefs.slice(at, at + 4)).toEqual([
      "/app/timeline",
      "/app/timeline?panel=cylinder",
      "/app/timeline?panel=plate",
      "/app/timeline?panel=rnaseq",
    ]);
  });

  it("marks the open panel's link, not Timeline itself", () => {
    renderAt("/app/timeline", "panel=plate");
    expect(current()).toEqual(["Plate scanner usage"]);
  });

  it("marks the first panel on the plain Timeline page", () => {
    renderAt("/app/timeline");
    expect(current()).toEqual(["Cylinder scanner usage"]);
  });

  it("marks RNA-seq runs on a run's page", () => {
    renderAt("/app/timeline/rnaseq/12");
    expect(current()).toEqual(["RNA-seq runs"]);
  });

  it("marks an ordinary page as before", () => {
    renderAt("/app/traits");
    expect(current()).toEqual(["Traits"]);
  });
});
