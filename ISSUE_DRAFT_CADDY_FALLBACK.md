# Caddy answers unknown hostnames with an empty 200 instead of rejecting them

<!-- DRAFT — not filed. Edit freely, then file when ready. -->

## What happens

Caddy serves a fixed list of hostnames, set by `CADDY_SITE_ADDRESSES`.

If a request arrives for a hostname that is **not** on that list, Caddy does not
return an error. It returns:

```
HTTP/1.1 200 OK
Content-Length: 0
```

A success code, with an empty page. That is Caddy's default behaviour when no
site block matches — not something we configured.

## Why that is a problem

A `200` means "here is what you asked for." Nothing was served, so it should say
so. Anything checking whether a hostname works — a test, a monitor, a deploy
smoke check — sees success and moves on.

It also makes a misconfiguration invisible. Remove a hostname from
`CADDY_SITE_ADDRESSES` by accident and nothing errors. The site just quietly
stops being served while still answering `200`.

## Where it caused a problem

`tests/integration/test_api_endpoints.py::test_studio_reachable` checked that
Supabase Studio was reachable, like this:

```python
req = urllib.request.Request("http://localhost/", headers={"Host": "studio.localhost"})
with urllib.request.urlopen(req, timeout=10) as resp:
    assert resp.status == 200
```

CI only listed one hostname, so `studio.localhost` matched nothing and got the
empty `200`. The test passed. It had been passing against nothing — it would
have passed with Studio deleted from the stack entirely.

Found during review of #649. Worked around there by listing all three hostnames
in CI and asserting a non-empty body. That fixes our test; it does not fix the
underlying behaviour, which will catch someone else out later.

## What to change

Add an explicit catch-all site block that refuses hostnames we do not serve —
`abort` (drop the connection) or `respond 404`. Then an unknown hostname fails
loudly instead of looking healthy.

Apply it to prod, staging and CI, so all environments behave the same. Right now
CI is the only place this is easy to hit.

**One wrinkle to work out.** On plain HTTP this is straightforward. On HTTPS a
catch-all needs a certificate for the hostname being refused, and we do not have
one for names we do not serve. In practice unknown HTTPS hostnames already fail
earlier, at the TLS handshake, because the name is not on our cert — so the
empty-200 mainly shows up over plain HTTP, which is what CI uses. Worth
confirming rather than assuming, and it may mean the catch-all only needs to
cover `:80`.


## Related

- #649 — where this was found and worked around in CI
- `caddy/Caddyfile` — the `{$CADDY_SITE_ADDRESSES}` site block
- `.github/workflows/pr-checks.yml` — where CI sets the hostname list
