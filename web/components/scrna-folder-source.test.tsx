// @vitest-environment jsdom
/**
 * The S3 folder field: it checks the folder once the URL is one, says it is checking,
 * shows what it found or why the folder can't be used, ignores a reply for a URL that has
 * since changed, and explains the accepted layout behind its "?".
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { useState } from "react";

vi.mock("@/lib/s3-folder", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/s3-folder")>()),
  FOLDER_CHECK_DELAY_MS: 0,
}));

import ScrnaFolderSource from "./scrna-folder-source";
import type { FolderCheck } from "@/lib/s3-folder";

function folder(url: string, sample = "col0"): FolderCheck {
  return {
    fastq_url: url,
    sample,
    lanes: [1, 2],
    files: [
      { name: `${sample}_S1_L001_R1_001.fastq.gz`, size: 1, etag: '"a"' },
      { name: `${sample}_S1_L001_R2_001.fastq.gz`, size: 1, etag: '"b"' },
      { name: `${sample}_S1_L002_R1_001.fastq.gz`, size: 1, etag: '"c"' },
      { name: `${sample}_S1_L002_R2_001.fastq.gz`, size: 38_200_000_000 - 3, etag: '"d"' },
    ],
    file_count: 4,
    total_bytes: 38_200_000_000,
  };
}

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

let fetchSpy: ReturnType<typeof vi.fn>;
let checked: (FolderCheck | null)[];

function Harness() {
  const [url, setUrl] = useState("");
  const [, setCheck] = useState<FolderCheck | null>(null);
  return (
    <ScrnaFolderSource
      url={url}
      onUrl={setUrl}
      onChecked={(c) => {
        checked.push(c);
        setCheck(c);
      }}
      fieldClass=""
      labelClass=""
    />
  );
}

function enter(url: string) {
  fireEvent.change(screen.getByLabelText("S3 folder URL"), { target: { value: url } });
}

beforeEach(() => {
  checked = [];
  fetchSpy = vi.fn(async (_url: string, init: RequestInit) =>
    json(folder(JSON.parse(String(init.body)).fastq_url))
  );
  vi.stubGlobal("fetch", fetchSpy);
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("checking the folder", () => {
  it("checks a folder URL and shows what it found", async () => {
    render(<Harness />);
    enter("s3://lab-data/run42");
    expect(screen.getByText("Checking the folder…")).toBeTruthy();
    expect(await screen.findByText("col0 · 2 lanes · 4 files · 38.2 GB")).toBeTruthy();
    const [url, init] = fetchSpy.mock.calls[0];
    expect(url).toBe("/api/scrna/cellranger/folder-check");
    expect(JSON.parse(String(init.body))).toEqual({ fastq_url: "s3://lab-data/run42/" });
    expect(checked.at(-1)?.fastq_url).toBe("s3://lab-data/run42/");
  });

  it("doesn't check something that isn't a folder URL, and says why", () => {
    render(<Harness />);
    enter("https://lab-data.s3.amazonaws.com/run42/");
    expect(screen.getByRole("alert").textContent).toBe("Enter an S3 folder, starting with s3://");
    enter("s3://lab-data/");
    expect(screen.getByRole("alert").textContent).toMatch(/looks like s3:\/\/bucket\/folder\//);
    expect(fetchSpy).not.toHaveBeenCalled();
    expect(checked.every((c) => c === null)).toBe(true);
  });

  it("shows why the service refused the folder, and reports no check", async () => {
    fetchSpy.mockResolvedValue(json({ detail: "This folder holds one sample; this one has a, b" }, 422));
    render(<Harness />);
    enter("s3://lab-data/run42/");
    expect((await screen.findByRole("alert")).textContent).toBe(
      "This folder holds one sample; this one has a, b"
    );
    expect(checked.at(-1)).toBeNull();
  });

  it("says so when the job service can't be reached", async () => {
    fetchSpy.mockRejectedValue(new TypeError("Failed to fetch"));
    render(<Harness />);
    enter("s3://lab-data/run42/");
    expect((await screen.findByRole("alert")).textContent).toBe("Could not reach the job service.");
  });

  it("forgets a passed check as soon as the URL changes", async () => {
    render(<Harness />);
    enter("s3://lab-data/run42/");
    await screen.findByText(/col0 · 2 lanes/);
    enter("s3://lab-data/run43/");
    expect(checked.at(-1)).toBeNull();
    expect(screen.getByText("Checking the folder…")).toBeTruthy();
  });

  it("ignores the reply for a URL that has since changed", async () => {
    let finishFirst: (r: Response) => void = () => {};
    fetchSpy
      .mockImplementationOnce(() => new Promise<Response>((resolve) => (finishFirst = resolve)))
      .mockImplementationOnce(async () => json(folder("s3://lab-data/run43/", "col1")));
    render(<Harness />);
    enter("s3://lab-data/run42/");
    await vi.waitFor(() => expect(fetchSpy).toHaveBeenCalledTimes(1));
    enter("s3://lab-data/run43/");
    expect(await screen.findByText(/col1 · 2 lanes/)).toBeTruthy();
    await act(async () => finishFirst(json(folder("s3://lab-data/run42/"))));
    expect(screen.queryByText(/col0 · 2 lanes/)).toBeNull();
    expect(checked.at(-1)?.sample).toBe("col1");
  });
});

describe("the accepted layout", () => {
  it("is shown behind the ? with an example folder", () => {
    render(<Harness />);
    const help = screen.getByRole("button", { name: "What folder to give" });
    expect(help.getAttribute("aria-expanded")).toBe("false");
    fireEvent.click(help);
    expect(help.getAttribute("aria-expanded")).toBe("true");
    expect(screen.getByText(/R1 and R2 for every lane/)).toBeTruthy();
    expect(screen.getByText(/other files/)).toBeTruthy();
    expect(screen.getByText(/col0_root_rep1_S1_L001_R1_001\.fastq\.gz/)).toBeTruthy();
    fireEvent.click(help);
    expect(screen.queryByText(/R1 and R2 for every lane/)).toBeNull();
  });
});
