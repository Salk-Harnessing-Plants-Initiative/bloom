/**
 * The consent screen must not leak its authorization_id via Referer.
 *
 * Supabase Auth redirects here with `?authorization_id=<id>` — a live,
 * unredeemed credential sitting in the page URL. Browsers attach the *full*
 * URL as `Referer` on same-origin requests under the default policy
 * (`strict-origin-when-cross-origin`), so every JS chunk, stylesheet and
 * image this page pulls in would carry that id. It goes nowhere today
 * because no access log is configured, but enabling one is an ordinary
 * change that would silently start recording live credentials.
 *
 * `referrer: 'no-referrer'` emits `<meta name="referrer" ...>`, which is
 * scoped to this page — deliberately not a site-wide Referrer-Policy, and
 * deliberately not a header. Caddy already sets Referrer-Policy at site
 * level; a second one from Next.js would arrive as a duplicate header, and
 * Referrer-Policy resolves last-wins, so the two would fight silently.
 */

import { describe, expect, it } from "vitest";

import { metadata } from "./page";

describe("consent page metadata", () => {
  it("suppresses the Referer entirely", () => {
    expect(
      metadata.referrer,
      "the authorization_id is in this page's query string — any policy that " +
        "sends the full URL same-origin propagates it into subresource requests",
    ).toBe("no-referrer");
  });
});
