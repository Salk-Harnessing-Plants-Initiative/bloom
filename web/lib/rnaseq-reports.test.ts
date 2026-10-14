import { describe, expect, it } from "vitest";
import {
  KEY_METRICS,
  keyMetrics,
  parseMetrics,
  readReport,
  reportHref,
  reportPath,
  storedReports,
} from "./rnaseq-reports";

// Cell Ranger's metrics_summary.csv: quoted values hold thousands separators.
const CSV = [
  "Estimated Number of Cells,Mean Reads per Cell,Median Genes per Cell,Number of Reads," +
    "Reads Mapped Confidently to Genome,Fraction Reads in Cells,Sequencing Saturation",
  '"5,066","27,129","1,642","137,437,089",91.2%,88.4%,61.0%',
  "",
].join("\n");

function storage({ names = [] as string[], listError = false, text = null as string | null }) {
  const asked: string[] = [];
  const bucket = {
    list: async (folder: string) => {
      asked.push(`list ${folder}`);
      return listError ? { data: null, error: { message: "denied" } } : { data: names.map((name) => ({ name })), error: null };
    },
    download: async (path: string) => {
      asked.push(`download ${path}`);
      return text === null ? { data: null, error: { message: "not found" } } : { data: new Blob([text]), error: null };
    },
  };
  return { client: { storage: { from: (b: string) => (asked.push(`bucket ${b}`), bucket) } } as never, asked };
}

describe("parseMetrics", () => {
  it("reads each metric, keeping a quoted value's commas", () => {
    const metrics = parseMetrics(CSV);
    expect(metrics.get("Estimated Number of Cells")).toBe("5,066");
    expect(metrics.get("Number of Reads")).toBe("137,437,089");
    expect(metrics.get("Sequencing Saturation")).toBe("61.0%");
  });

  it("reads Windows line ends and doubled quotes", () => {
    expect(parseMetrics('A,B\r\n"x ""y""",2\r\n').get("A")).toBe('x "y"');
  });

  it("is empty for a file without a line of values", () => {
    expect(parseMetrics("A,B\n").size).toBe(0);
    expect(parseMetrics("").size).toBe(0);
  });
});

describe("keyMetrics", () => {
  it("gives the key metrics in order, leaving out any the run didn't report", () => {
    expect(keyMetrics(parseMetrics(CSV))).toEqual([
      ["Estimated Number of Cells", "5,066"],
      ["Median Genes per Cell", "1,642"],
      ["Mean Reads per Cell", "27,129"],
      ["Reads Mapped Confidently to Genome", "91.2%"],
      ["Fraction Reads in Cells", "88.4%"],
      ["Sequencing Saturation", "61.0%"],
    ]);
    expect(keyMetrics(parseMetrics("Estimated Number of Cells\n12\n"))).toEqual([["Estimated Number of Cells", "12"]]);
    expect(KEY_METRICS).toHaveLength(6);
  });
});

describe("a run's stored reports", () => {
  it("lists them under reports/<run id> in the scrna bucket", async () => {
    const { client, asked } = storage({ names: ["web_summary.html", "metrics_summary.csv"] });
    expect([...(await storedReports(client, 8))].sort()).toEqual(["metrics_summary.csv", "web_summary.html"]);
    expect(asked).toEqual(["bucket scrna", "list reports/8"]);
  });

  it("is none when the folder can't be read", async () => {
    expect((await storedReports(storage({ listError: true }).client, 8)).size).toBe(0);
  });

  it("reads one, or null when it isn't there", async () => {
    const { client, asked } = storage({ text: "<html>8</html>" });
    expect(await readReport(client, 8, "web_summary.html")).toBe("<html>8</html>");
    expect(asked).toContain("download reports/8/web_summary.html");
    expect(await readReport(storage({}).client, 8, "web_summary.html")).toBeNull();
  });

  it("names the paths and the report page from the run id", () => {
    expect(reportPath(8, "web_summary.html")).toBe("reports/8/web_summary.html");
    expect(reportHref(8)).toBe("/app/rnaseq-runs/8/report");
  });
});
