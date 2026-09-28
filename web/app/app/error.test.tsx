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

  it("tries again by reloading from the server when Next offers that", () => {
    const reset = vi.fn();
    const retry = vi.fn();
    render(<AppError error={new Error("boom")} reset={reset} retry={retry} />);

    fireEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(retry).toHaveBeenCalledTimes(1);
    expect(reset).not.toHaveBeenCalled();
  });

  it("names a server failure in plain words, with the reference to find it by", () => {
    // In production Next replaces a server error's message with its own
    // sentence; the digest is what matches it to the server's logs.
    const error = Object.assign(new Error("An error occurred in the Server Components render."), {
      digest: "3141592653",
    });
    render(<AppError error={error} reset={vi.fn()} />);

    expect(screen.getByText("The server could not build this page.")).toBeTruthy();
    expect(screen.getByText("Reference: 3141592653")).toBeTruthy();
    expect(screen.queryByText(/Server Components render/)).toBeNull();
  });
});
