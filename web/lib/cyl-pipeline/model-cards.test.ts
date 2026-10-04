/** The confirm dialog's model-card read (bloom#971): shape check and fetch. */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { PRODUCTION_CARDS } from "./__fixtures__/model-cards";
import { MODEL_CARDS_TIMEOUT_MS, MODEL_CARDS_URL, fetchModelCards, isCardList } from "./model-cards";

const body = (cards: unknown = PRODUCTION_CARDS, skipped: unknown = 0) => ({
  cards,
  fetched_at: "2026-10-02T12:00:00+00:00",
  skipped,
});
const okResponse = (payload: unknown = body()) =>
  new Response(JSON.stringify(payload), { status: 200, headers: { "Content-Type": "application/json" } });

describe("isCardList", () => {
  it("accepts the production cards", () => {
    expect(isCardList(body())).toBe(true);
    expect(isCardList(body([]))).toBe(true);
  });

  const card = PRODUCTION_CARDS[0];
  const sel = card.selectors[0];
  it.each([
    ["a non-integer age_max", body([{ ...card, selectors: [{ ...sel, age_max: 13.5 }] }])],
    ["a boolean age_max", body([{ ...card, selectors: [{ ...sel, age_max: true }] }])],
    ["a NaN age_min", body([{ ...card, selectors: [{ ...sel, age_min: NaN }] }])],
    ["a null card", body([null])],
    ["a selector missing species", body([{ ...card, selectors: [{ mode: "cylinder", age_min: 2, age_max: 14 }] }])],
    ["a missing selectors", body([{ root_type: "primary", registry_id: "r", version: "v0" }])],
    ["a non-array cards", body({})],
    ["a missing fetched_at", { cards: PRODUCTION_CARDS, skipped: 0 }],
    ["a missing skipped", { cards: PRODUCTION_CARDS, fetched_at: "t" }],
    ["a negative skipped", body(PRODUCTION_CARDS, -1)],
    ["a fractional skipped", body(PRODUCTION_CARDS, 0.5)],
    ["null", null],
  ])("rejects %s", (_label, value) => {
    expect(isCardList(value)).toBe(false);
  });
});

describe("fetchModelCards", () => {
  let fetchSpy: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    fetchSpy = vi.fn();
    vi.stubGlobal("fetch", fetchSpy);
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  it("returns the cards from the proxy", async () => {
    fetchSpy.mockResolvedValue(okResponse());
    await expect(fetchModelCards()).resolves.toEqual({ cards: PRODUCTION_CARDS, skipped: 0 });
    expect(fetchSpy.mock.calls[0][0]).toBe(MODEL_CARDS_URL);
  });

  it("returns an empty list as an empty list", async () => {
    fetchSpy.mockResolvedValue(okResponse(body([], 2)));
    await expect(fetchModelCards()).resolves.toEqual({ cards: [], skipped: 2 });
  });

  it.each([
    ["a 502", () => new Response(JSON.stringify({ detail: "x" }), { status: 502 })],
    ["a bad shape", () => okResponse({ cards: "nope", fetched_at: "t", skipped: 0 })],
    ["a non-JSON body", () => new Response("<html>", { status: 200 })],
  ])("returns null for %s", async (_label, make) => {
    fetchSpy.mockResolvedValue(make());
    await expect(fetchModelCards()).resolves.toBeNull();
  });

  it("returns null when the fetch rejects", async () => {
    fetchSpy.mockRejectedValue(new TypeError("Failed to fetch"));
    await expect(fetchModelCards()).resolves.toBeNull();
  });

  it("gives up after 10 seconds when the proxy never answers", async () => {
    vi.useFakeTimers();
    expect(MODEL_CARDS_TIMEOUT_MS).toBe(10_000);
    fetchSpy.mockImplementation(
      (_url: string, init: RequestInit) =>
        new Promise((_resolve, reject) => {
          init.signal?.addEventListener("abort", () => reject(init.signal?.reason));
        }),
    );
    let settled: unknown = "pending";
    const result = fetchModelCards().then((value) => {
      settled = value;
    });
    await vi.advanceTimersByTimeAsync(9_999);
    expect(settled).toBe("pending");
    await vi.advanceTimersByTimeAsync(1);
    await result;
    expect(settled).toBeNull();
  });

  it("gives up after 10 seconds when the body never arrives", async () => {
    vi.useFakeTimers();
    fetchSpy.mockResolvedValue({ ok: true, status: 200, json: () => new Promise(() => {}) });
    const result = fetchModelCards();
    await vi.advanceTimersByTimeAsync(10_000);
    await expect(result).resolves.toBeNull();
  });

  it("leaves no timer behind after a successful read", async () => {
    vi.useFakeTimers();
    fetchSpy.mockResolvedValue(okResponse());
    await fetchModelCards();
    expect(vi.getTimerCount()).toBe(0);
  });
});
