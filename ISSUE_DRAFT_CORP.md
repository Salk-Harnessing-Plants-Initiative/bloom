# Consider adding Cross-Origin-Resource-Policy

<!-- DRAFT — not filed. Edit freely, then file when ready. -->

## What this header does

`Cross-Origin-Resource-Policy` controls **who is allowed to load our files** —
images, fonts, scripts — into their own web pages.

Without it, any website can point an `<img>` tag at a Bloom image and display it
on their page. With it, the browser refuses unless the request comes from us.

It takes one of three values. There is no list of allowed sites — the header does
not support that:

| Value | Who can load our files |
| --- | --- |
| `same-origin` | Only the exact same hostname |
| `same-site` | Us and our own subdomains |
| `cross-origin` | Anyone (this is the default today) |

## Why it was left out of #649

#649 only added headers that could not break anything. This one looked like it
might, so it was left for its own change.

The concern was that `web/next.config.js` lists image sources on a **different
hostname** than the app runs on (`api.bloom.salk.edu`,
`api.bloom-staging.salkhpi.org`). If images loaded from there, `same-origin`
would block Bloom's own plant images, plate photos and scan thumbnails.

## That concern does not hold

Those entries are `remotePatterns`, which only apply to Next.js's `<Image>`
component. Bloom does not use it for storage images:

- `web/components/plant-image.tsx` — the `next/image` import is commented out
  (line 6); it renders a plain `<img>` (line 59)
- `web/components/illustration.tsx` — plain `<img>`, with the Next.js lint rule
  explicitly disabled (lines 64, 75)

The only file importing `next/image` outside `_examples/` is `plant-image.tsx`,
where it is commented out. So `remotePatterns` governs nothing here, and no
runtime code references those `api.*` hostnames — they appear only in
`next.config.js` and in comments.

Image URLs are built by the Supabase client from `NEXT_PUBLIC_SUPABASE_URL`, and
in both environments that is the same origin the app is served from:

```
prod     app  https://bloom.salk.edu               storage  https://bloom.salk.edu/api
staging  app  https://staging.bloom.salk.edu:8443  storage  https://staging.bloom.salk.edu:8443/api
```

Same hostname, same port, same scheme. Images are already same-origin.

## Confirmed on staging

Checked directly against the running staging deployment, not inferred:

**1. The app reports its own storage URL as the same origin it runs on.**
`GET https://staging.bloom.salk.edu:8443/api/client-info` returns:

```json
{"api_url": "https://staging.bloom.salk.edu:8443/api"}
```

Same scheme, same host, same port as the page itself. The Supabase client is
built from this value, so every storage image URL is same-origin by construction.

**2. Every resource the app loads is same-origin.** Loading the staging login
page produced 30 requests — HTML, fonts, all JS and CSS chunks, and 11 images —
and every single one came from `staging.bloom.salk.edu:8443`. No `api.*`
hostname appeared at all.

So `Cross-Origin-Resource-Policy: same-origin` would not break image loading. The
concern that deferred it does not apply.

## If we go ahead

One line in the site-level header block in `caddy/Caddyfile`, alongside the
others. The existing tests pick up new headers automatically — only the expected
value lists need updating.

Worth noting `next.config.js`'s `remotePatterns` appear to be unused. Cleaning
them up is separate from this, but the two are worth looking at together.

## Related

- #649 — the security headers change this was split out of
- `web/next.config.js` — the image source list that raised the concern
