// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";

import { TransgeneToggle } from "./transgene-toggle";

afterEach(cleanup);

describe("the transgene counts switch", () => {
  it("says whether the counts are shown, and turns them off", () => {
    const onChange = vi.fn();
    render(<TransgeneToggle on onChange={onChange} />);
    const toggle = screen.getByRole("switch", { name: "Transgene counts" });
    expect(toggle.getAttribute("aria-checked")).toBe("true");
    fireEvent.click(toggle);
    expect(onChange).toHaveBeenCalledWith(false);
  });

  it("turns them back on", () => {
    const onChange = vi.fn();
    render(<TransgeneToggle on={false} onChange={onChange} />);
    const toggle = screen.getByRole("switch", { name: "Transgene counts" });
    expect(toggle.getAttribute("aria-checked")).toBe("false");
    fireEvent.click(toggle);
    expect(onChange).toHaveBeenCalledWith(true);
  });
});
