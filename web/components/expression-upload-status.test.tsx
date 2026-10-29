// @vitest-environment jsdom
import { afterEach, describe, expect, it } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";

import {
  IncompleteUploadBadge,
  IncompleteUploadNotice,
  isIncompleteUpload,
} from "./expression-upload-status";

afterEach(cleanup);

describe("which datasets count as an incomplete upload", () => {
  it("is one that records its file but no finish time", () => {
    expect(isIncompleteUpload({ source_checksum: "abc", ingested_at: null })).toBe(true);
  });

  it("is not a finished one", () => {
    expect(isIncompleteUpload({ source_checksum: "abc", ingested_at: "2026-10-05T00:00:00Z" })).toBe(
      false,
    );
  });

  it("is not one from a loader that records neither", () => {
    expect(isIncompleteUpload({ source_checksum: null, ingested_at: null })).toBe(false);
  });

  it("is not one with a finish time but no file", () => {
    expect(isIncompleteUpload({ source_checksum: null, ingested_at: "2026-10-05T00:00:00Z" })).toBe(
      false,
    );
  });
});

describe("the incomplete-upload notice", () => {
  it("says the dataset is incomplete or still being uploaded", () => {
    render(<IncompleteUploadNotice />);
    expect(screen.getByRole("note").textContent).toContain(
      "This dataset is incomplete or is still being uploaded",
    );
  });
});

describe("the incomplete-upload badge", () => {
  it("names the state", () => {
    render(<IncompleteUploadBadge />);
    expect(screen.getByText("Incomplete upload")).toBeTruthy();
  });
});
