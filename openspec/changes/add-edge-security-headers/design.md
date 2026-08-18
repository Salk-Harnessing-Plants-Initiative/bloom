## Context

Caddy fronts every hostname in the stack from a single site block whose listen addresses come from `CADDY_SITE_ADDRESSES`. Per-hostname routing happens inside that block via `@matcher host X` + `handle @matcher` for `DOMAIN_MAIN`, `DOMAIN_STUDIO`, and `DOMAIN_MINIO`.

Two of those hostnames serve third-party UIs we do not control (Supabase Studio, MinIO console). One serves bloom-web plus several non-browser API surfaces (`/api/*` → Kong, `/langchain/*`, `/bloommcp/*`, `/workflows/*`).

The driver is issue #108 item 1, brought forward by the plan to expose bloom-web beyond Salk's network. Today an attacker who steals a session cannot use it, because they cannot reach Bloom. That is the control currently doing the work; these headers begin replacing it.

## Goals / Non-Goals

**Goals**

- Set the security headers on every hostname Caddy serves, from a single declaration, having verified each surface against them.
- Carry no risk of refusing an asset on any surface, rather than deferring that risk to a post-deploy check.
- Keep the change revertible with no client-side residue.
- Establish the `edge-security-headers` capability that HSTS and CSP will extend.

**Non-Goals**

- CSP `script-src` and HSTS (separate changes, deliberately). `frame-ancestors` ships here; it is the one CSP directive that needs no nonce work.
- Fixing the DOM XSS sink at `web/components/expression-multigene-dotplot.tsx:276`, where a D3 datum is interpolated into `innerHTML`. That is a real bug, not a header concern, and is tracked separately — CSP should not be used to paper over it.
- `Content-Disposition` hardening for user-uploaded objects served via `/api/storage/v1/*`. `nosniff` does not address attacker-declared `Content-Type`; that needs its own change.
- Rate limiting (#108 item 2) and Studio/MinIO access control (#108 item 6).

## Decisions

**Decision: Declare the headers once at site level, covering all three hostnames.**
One `header` block after the `tls` directive and ahead of the `@main`/`@studio`/`@minio` matchers, so every hostname inherits it from a single declaration.

The deciding factor is Supabase Studio. It is an administrative console whose access control is tracked separately (#108 item 6), so the anti-framing headers are load-bearing there in a way they are not on the main hostname. Scoping the block to the main hostname would leave that gap open while looking complete.

The consoles were measured before being covered, because `nosniff` causes a browser to *hard-refuse* a script whose `Content-Type` is not a JavaScript type, or a stylesheet that is not `text/css` — extending coverage to a UI we do not build would otherwise be unsafe to assume. Against the image pins prod actually runs, both are clean:

| Surface | Image | Result |
| --- | --- | --- |
| Supabase Studio | `2026.03.30-sha-12a43e5` | 92/92 assets correctly typed (`application/javascript`, `text/css`) |
| MinIO console | `RELEASE.2025-01-20T14-49-07Z` | 3/3 correctly typed |

Neither serves nor dynamically creates an `<iframe>`, so `X-Frame-Options: DENY` does not break them either.

For bloom-web the same risk was checked and eliminated (see the pre-flight in `tasks.md`): no dynamic script or stylesheet loading exists, Next.js types its own assets correctly, storage objects are consumed as images, and client-side exports never traverse Caddy.

Site level also means Caddy applies the headers ahead of the handler chain, so synthetic responses and upstream-error responses carry them too — a per-route copy would have to remember each one.

*Alternatives considered:*
- *A `header` block inside `handle @main`* — the original shape of this change, rejected on review. It leaves Studio and MinIO bare, which matters precisely because those consoles have their own tracked access-control work, and it needs a comment justifying an exclusion that the measurement above shows is unnecessary. Site level deletes both the gap and the explanation.
- *Site-level placement minus `nosniff` for the console hostnames* — moot given the measurement; it existed only to route around a risk that turned out not to exist.
- *Relying on network-level reachability limits instead* — rejected as a reason to omit the headers: that is a control which can change without anyone revisiting this file, and the headers cost nothing to apply.

**Decision: Include `Content-Security-Policy: frame-ancestors 'none'` alongside `X-Frame-Options`.**
`frame-ancestors` is the standards-track directive; XFO is the legacy one that older clients still honour. Sending both covers each population, and neither depends on the nonce work that blocks `script-src`.

On the MinIO hostname this arrives beside the console's own CSP. Browsers enforce every CSP header present, admitting only what all of them allow, so the pair is strictly more restrictive than either alone — `frame-ancestors 'none'` holds and MinIO's `default-src`/`script-src` policy is untouched. This is the one duplicated header whose two values differ; the other three duplicate byte-identically.

**Decision: Set the headers at the edge, not in `next.config.js`.**
Caddy covers Studio and MinIO too; Next.js would cover only bloom-web.
*Alternatives considered:* Next.js `headers()`, rejected as strictly narrower coverage for the same effort.

**Decision: `X-Frame-Options: DENY`, not `SAMEORIGIN`.**
The header governs who may frame Bloom, not what Bloom may frame. `web/app/app/orthofinder/page.tsx:32` embeds an external OrthoBrowser — that is outbound and unaffected. No bloom-web page frames another bloom-web page, so `DENY` costs nothing over `SAMEORIGIN` and closes same-origin framing chains as well.
*Alternatives considered:* `SAMEORIGIN`, which would be the safer default if Studio or MinIO self-frames. Chosen against because the failure is immediate, obvious, and revertible; see Risks.

**Decision: `Cross-Origin-Opener-Policy: same-origin-allow-popups`, not `same-origin`.**
The anti-framing headers cover embedding; neither covers a page that opens Bloom with `window.open` and keeps a scriptable handle to that window. COOP severs that handle.

`allow-popups` rather than the stricter `same-origin` because this block also covers Studio and the MinIO console. It severs the link when another site opens one of ours — the attack — while leaving popups those consoles open themselves working, so no measurement of third-party UIs is required to ship it safely. bloom-web calls `window.open` nowhere, so the two values are identical for it.
*Alternatives considered:* `same-origin`, which additionally enables cross-origin isolation. Rejected as unnecessary — nothing here needs `SharedArrayBuffer` — and it would put unmeasured constraints on two consoles this project does not build.

**Decision: `Cross-Origin-Resource-Policy: same-origin`.**
Stops another site loading Bloom's images and files into its own pages. Deferred at first, on the grounds that `web/next.config.js` lists image sources on `api.bloom.salk.edu` and `api.bloom-staging.salkhpi.org`, which `same-origin` would block. Checked rather than assumed, and the concern does not hold:

- Those entries are `remotePatterns`, which apply only to Next.js `<Image>`. Storage images do not use it — `plant-image.tsx` has the `next/image` import commented out and renders a plain `<img>`, and `illustration.tsx` does the same. No runtime code references either `api.*` host.
- Both deployments report storage on the origin they are served from. `/api/client-info` returns `https://bloom.salk.edu/api` on prod and `https://staging.bloom.salk.edu:8443/api` on staging — same scheme, host and port as the app in each case.
- Loading the staging login page issued 30 requests — HTML, fonts, every JS and CSS chunk, 11 images — all from the app's own origin.

The header restricts others loading our resources, not us loading theirs, so the outbound OrthoBrowser iframe and JBrowse's S3 data are unaffected at any value.
*Alternatives considered:* `same-site`, which would also permit subdomains. Unnecessary given the measurement, and strictly weaker. An origin allow-list is not possible — the header takes only `same-origin`, `same-site` or `cross-origin`; per-origin control belongs in CSP `img-src` on the consuming page.

**Decision: `Permissions-Policy` restricts only `camera`, `microphone`, `geolocation`.**
`fullscreen` is deliberately left enabled: the OrthoBrowser iframe sets `allowFullScreen`, and restricting it would break that page.
*Alternatives considered:* a broader deny-list covering every powerful feature. Rejected — the marginal benefit is near zero, and each additional entry is another chance to break a working surface.

## Risks / Trade-offs

- **Third-party consoles breaking under `nosniff` or `DENY`.** The headers now do reach them, so this is a live risk rather than a hypothetical one. → Measured clean against the pins production runs (table above), and re-measurement is required if either pin moves — the spec makes that a precondition rather than a suggestion.
- **A future refactor moves the block under a host matcher.** Studio and MinIO would silently lose every header, with no error and no failing request — the failure is invisible from the outside. → `tests/unit/test_caddy_security_headers.py` locates the block by brace-matched depth and fails if it moves inside a nested `handle`.
- **An upstream starts emitting one of these headers a second time.** `Referrer-Policy` and `Permissions-Policy` resolve last-wins, so a *differing* upstream value silently replaces the edge's. `Cross-Origin-Opener-Policy` and `Cross-Origin-Resource-Policy` are worse than last-wins: repeated field lines join with a comma, which is neither a legal CORP value nor a single structured-field item, so **any** duplicate — byte-identical included — makes the browser drop the policy entirely. A presence-only check reports both states as healthy. → The integration test asserts with `get_all` and pins exact values, and holds those two to exactly one occurrence on every route and both consoles.
- **`Permissions-Policy` has uneven browser support and a syntax that changed during standardisation.** → Unsupported browsers ignore it; the syntax used here is the current structured form. Low impact either way given it is the weakest of the set.
- **These headers create an impression of coverage they do not provide.** They do not address server-side compromise (e.g. a CVE reached through 443), which is what actually happened to this host previously. → Recorded here explicitly; the mitigations for that class are image patching, surface reduction, and rate limiting, none of which this change touches.

## Migration Plan

1. Merge; Caddy picks the config up on reload — no image rebuild required.
2. Verify every header in the block is present on the main hostname (`curl -I`).
3. Verify they are present on the Studio and MinIO hostnames too, confirming the site-level declaration is inherited rather than main-only.
4. Confirm bloom-web renders normally, with no blocked script or stylesheet errors in the browser console.
5. **Rollback:** revert the commit and reload. No client-cached state, so rollback is immediate and complete.

## Open Questions

- Should CI exercise the multi-hostname claim? Asked, answered no, then reversed on review. It turned out to cost one line — the committed `.env.ci` already listed all three hostnames and the workflow was overwriting it with one. CI now serves all three and asserts every header on both consoles. See task 2.10.
- Does Studio's access-control gap warrant its own fix? The anti-framing headers mitigate a symptom, not the cause. Pre-existing and filed separately — #108 item 6's IP allowlist is the durable backstop.
