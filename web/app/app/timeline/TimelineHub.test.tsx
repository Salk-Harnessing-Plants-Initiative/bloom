// @vitest-environment jsdom
/** The Timeline hub: opening another panel changes the URL; closing the open one folds it up. */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";

const push = vi.fn();
vi.mock("next/navigation", () => ({ useRouter: () => ({ push }) }));

import TimelineHub from "./TimelineHub";

beforeEach(() => push.mockClear());
afterEach(cleanup);

function header(label: string) {
  return screen.getByRole("button", { name: label });
}

describe("TimelineHub", () => {
  it("opens the panel from the URL with its content", () => {
    render(<TimelineHub open="rnaseq"><p>Runs list</p></TimelineHub>);
    expect(header("RNA-seq runs").getAttribute("aria-expanded")).toBe("true");
    expect(screen.getByText("Runs list")).toBeTruthy();
  });

  it("goes to another panel's URL when its header is clicked", () => {
    render(<TimelineHub open="cylinder"><p>Batches</p></TimelineHub>);
    fireEvent.click(header("Plate scanner usage tracking"));
    expect(push).toHaveBeenCalledWith("/app/timeline?panel=plate", { scroll: false });
  });

  it("closes and reopens the open panel without changing the URL", () => {
    render(<TimelineHub open="cylinder"><p>Batches</p></TimelineHub>);
    const cylinder = header("Cylinder scanner usage tracking");
    fireEvent.click(cylinder);
    expect(cylinder.getAttribute("aria-expanded")).toBe("false");
    expect(screen.queryByText("Batches")).toBeNull();
    fireEvent.click(cylinder);
    expect(cylinder.getAttribute("aria-expanded")).toBe("true");
    expect(screen.getByText("Batches")).toBeTruthy();
    expect(push).not.toHaveBeenCalled();
  });
});
