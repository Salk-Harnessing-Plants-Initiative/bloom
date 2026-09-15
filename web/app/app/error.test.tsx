// @vitest-environment jsdom
/** A page that fails lands on one that says what failed and offers to try again. */

import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";

import AppError from "./error";

afterEach(cleanup);

describe("the app error page", () => {
  it("says what failed and offers to try again", () => {
    const reset = vi.fn();
    render(<AppError error={new Error("the map could not be read")} reset={reset} />);

    expect(screen.getByText(/Something went wrong on this page/)).toBeTruthy();
    expect(screen.getByText(/the map could not be read/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(reset).toHaveBeenCalledTimes(1);
  });
});
