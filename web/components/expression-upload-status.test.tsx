// @vitest-environment jsdom
import { afterEach, describe, expect, it } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";

import {
  IncompleteUploadBadge,
  IncompleteUploadNotice,
  expectedCells,
  isIncompleteUpload,
} from "./expression-upload-status";

afterEach(cleanup);

const FINISHED = { source_checksum: "abc", ingested_at: "2026-10-05T00:00:00Z", metadata: {} };

describe("which datasets count as an incomplete upload", () => {
  it("is a dataset that records its file but no finish time", () => {
    expect(isIncompleteUpload({ ...FINISHED, ingested_at: null })).toBe(true);
  });

  it("is not a finished dataset", () => {
    expect(isIncompleteUpload(FINISHED)).toBe(false);
  });

  it("is not a dataset loaded before files were recorded, which has neither", () => {
    expect(isIncompleteUpload({ source_checksum: null, ingested_at: null, metadata: null })).toBe(
      false,
    );
  });
});

describe("the expected cell count", () => {
  it("is read from what the upload recorded", () => {
    expect(expectedCells({ ...FINISHED, metadata: { expected_cells: 40000 } })).toBe(40000);
  });

  it("is unknown when nothing was recorded, or something else was", () => {
    expect(expectedCells({ ...FINISHED, metadata: null })).toBeNull();
    expect(expectedCells({ ...FINISHED, metadata: { expected_cells: "40000" } })).toBeNull();
    expect(expectedCells({ ...FINISHED, metadata: [1, 2] })).toBeNull();
  });
});

describe("the incomplete-upload notice", () => {
  it("says how far the upload got and how to finish it", () => {
    render(<IncompleteUploadNotice loadedCells={5000} expected={40000} />);
    const notice = screen.getByRole("status");
    expect(notice.textContent).toContain("5,000 of 40,000 cells are loaded");
    expect(notice.textContent).toContain("bloomctl scrna hdf5 upload");
  });

  it("says how many are loaded when the total was not recorded", () => {
    render(<IncompleteUploadNotice loadedCells={5000} expected={null} />);
    expect(screen.getByRole("status").textContent).toContain("5,000 cells are loaded so far");
  });

  it("still warns when the count could not be read", () => {
    render(<IncompleteUploadNotice loadedCells={null} expected={40000} />);
    expect(screen.getByRole("status").textContent).toContain("Not every cell is loaded");
  });
});

describe("the incomplete-upload badge", () => {
  it("names the state", () => {
    render(<IncompleteUploadBadge />);
    expect(screen.getByText("Incomplete upload")).toBeTruthy();
  });
});
