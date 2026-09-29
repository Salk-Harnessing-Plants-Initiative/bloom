/**
 * Local validation for POST /api/cyl/pipeline (spec: "Trigger proxy validates
 * the body locally and forwards a rebuilt body").
 *
 * The rebuilt body is the point: nothing the client sent is spread into what
 * goes upstream, and `params` is always `{}` while overrides are inert (#897).
 */

import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

import { MAX_TRIGGER_SCAN_IDS, parseTriggerRequest } from "./trigger-request";

function ids(n: number): number[] {
  return Array.from({ length: n }, (_, i) => i + 1);
}

function expectRejected(raw: unknown) {
  const result = parseTriggerRequest(raw);
  expect(result.ok).toBe(false);
  if (!result.ok) expect(result.detail).toEqual(expect.any(String));
}

describe("MAX_TRIGGER_SCAN_IDS", () => {
  it("is 5000", () => {
    expect(MAX_TRIGGER_SCAN_IDS).toBe(5000);
  });

  // The trigger enforces its own cap; this one refuses the same lists locally.
  // Reading the source keeps the two from drifting.
  it("equals MAX_SCAN_IDS in services/workflows/pipeline.py", () => {
    const source = readFileSync(
      fileURLToPath(new URL("../../../services/workflows/pipeline.py", import.meta.url)),
      "utf8"
    );
    const match = source.match(/^MAX_SCAN_IDS\s*=\s*([\d_]+)\s*$/m);
    expect(match).not.toBeNull();
    expect(Number(match![1].replace(/_/g, ""))).toBe(MAX_TRIGGER_SCAN_IDS);
  });
});

describe("rebuilt body", () => {
  it("drops client params and extra fields for a single-target level", () => {
    expect(
      parseTriggerRequest({ target_level: "scan", target_id: 42, params: { age: 7 }, extra: 1 })
    ).toEqual({ ok: true, body: { target_level: "scan", target_id: 42, params: {} } });
  });

  it("accepts wave and experiment targets", () => {
    for (const level of ["wave", "experiment"]) {
      expect(parseTriggerRequest({ target_level: level, target_id: 3 })).toEqual({
        ok: true,
        body: { target_level: level, target_id: 3, params: {} },
      });
    }
  });

  it("forwards no target_id for scan_ids, whether it was null or absent", () => {
    const expected = {
      ok: true,
      body: { target_level: "scan_ids", scan_ids: [4, 5], params: {} },
    };
    expect(parseTriggerRequest({ target_level: "scan_ids", target_id: null, scan_ids: [4, 5] })).toEqual(expected);
    expect(parseTriggerRequest({ target_level: "scan_ids", scan_ids: [4, 5], params: { x: 1 } })).toEqual(expected);
  });

  it("does not alias the caller's scan_ids array", () => {
    const scanIds = [1, 2];
    const result = parseTriggerRequest({ target_level: "scan_ids", scan_ids: scanIds });
    if (!result.ok || result.body.target_level !== "scan_ids") throw new Error("expected a scan_ids body");
    expect(result.body.scan_ids).toEqual(scanIds);
    expect(result.body.scan_ids).not.toBe(scanIds);
  });
});

describe("body shape", () => {
  it("rejects a non-object body", () => {
    for (const raw of [null, [], "scan", 42, true]) expectRejected(raw);
  });
});

describe("target_level", () => {
  it("rejects a missing or unknown level", () => {
    expectRejected({ target_id: 1 });
    expectRejected({ target_level: "plant", target_id: 1 });
    expectRejected({ target_level: "SCAN", target_id: 1 });
  });
});

describe("target_id for scan, wave and experiment", () => {
  it("rejects anything but a safe positive integer", () => {
    for (const target_id of ["42", true, 0, -1, 1.5, 9007199254740993, null, undefined, NaN, Infinity]) {
      expectRejected({ target_level: "scan", target_id });
    }
  });

  // Per the spec's Number.isSafeInteger: JSON 42.0 and 1e2 are integers once
  // parsed, so they pass and are forwarded as integers (see the module header).
  it("accepts integer-valued JSON numbers written as floats", () => {
    expect(parseTriggerRequest(JSON.parse('{"target_level": "scan", "target_id": 42.0}'))).toEqual({
      ok: true,
      body: { target_level: "scan", target_id: 42, params: {} },
    });
    expect(parseTriggerRequest(JSON.parse('{"target_level": "scan_ids", "scan_ids": [1e2]}'))).toEqual({
      ok: true,
      body: { target_level: "scan_ids", scan_ids: [100], params: {} },
    });
  });

  it("accepts the largest safe integer", () => {
    expect(parseTriggerRequest({ target_level: "scan", target_id: Number.MAX_SAFE_INTEGER }).ok).toBe(true);
  });

  it("rejects scan_ids alongside it", () => {
    expectRejected({ target_level: "experiment", target_id: 5, scan_ids: [1] });
    expectRejected({ target_level: "scan", target_id: 5, scan_ids: null });
  });
});

describe("scan_ids", () => {
  it("rejects a target_id alongside it", () => {
    expectRejected({ target_level: "scan_ids", target_id: 3, scan_ids: [1] });
    expectRejected({ target_level: "scan_ids", target_id: 0, scan_ids: [1] });
  });

  it("accepts exactly MAX_TRIGGER_SCAN_IDS entries with a null target_id", () => {
    const result = parseTriggerRequest({
      target_level: "scan_ids",
      target_id: null,
      scan_ids: ids(MAX_TRIGGER_SCAN_IDS),
    });
    expect(result.ok).toBe(true);
    expect(result.ok && result.body.target_level === "scan_ids" && result.body.scan_ids.length).toBe(5000);
  });

  it("rejects MAX_TRIGGER_SCAN_IDS + 1 entries", () => {
    expectRejected({ target_level: "scan_ids", target_id: null, scan_ids: ids(MAX_TRIGGER_SCAN_IDS + 1) });
  });

  it("rejects an empty, missing or non-array value", () => {
    expectRejected({ target_level: "scan_ids", scan_ids: [] });
    expectRejected({ target_level: "scan_ids" });
    expectRejected({ target_level: "scan_ids", scan_ids: null });
    expectRejected({ target_level: "scan_ids", scan_ids: { 0: 1 } });
  });

  it("rejects any entry that is not a safe positive integer", () => {
    for (const bad of ["1", true, 0, -1, 1.5, 9007199254740993, null]) {
      expectRejected({ target_level: "scan_ids", scan_ids: [1, bad] });
    }
  });
});
