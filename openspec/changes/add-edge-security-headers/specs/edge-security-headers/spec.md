## ADDED Requirements

### Requirement: MIME Type Sniffing Suppression

Every HTTP response Caddy serves SHALL carry `X-Content-Type-Options: nosniff`, so a browser never overrides a declared `Content-Type` by inspecting response bytes.

#### Scenario: Header present on every response

- **WHEN** a client requests any path on any hostname Caddy serves
- **THEN** the response carries `X-Content-Type-Options: nosniff`

#### Scenario: Declared type is honoured for stored objects

- **WHEN** a stored object whose bytes resemble HTML is served with a non-HTML `Content-Type`
- **THEN** the browser treats it as the declared type
- **AND** does not reinterpret it as HTML or execute script within it

#### Scenario: No covered surface depends on sniffing to load

- **WHEN** a covered hostname serves a script, stylesheet, image, or downloadable export
- **THEN** it declares a `Content-Type` the browser accepts for that use without sniffing
- **AND** no script or stylesheet is refused as a result of the header

#### Scenario: Non-browser callers are structurally unaffected

- **WHEN** bloomctl or another non-browser client retrieves a file through Caddy
- **THEN** the `Content-Type` sent is unchanged by this header
- **AND** the client's handling is identical, because sniffing is a browser behaviour only

### Requirement: Frame Embedding Denial

Every HTTP response Caddy serves SHALL carry `X-Frame-Options: DENY`, so no other page may embed a Bloom page in a frame.

#### Scenario: Cross-origin framing refused

- **WHEN** a page on another origin embeds a Bloom URL in an iframe
- **THEN** the browser refuses to render the framed content
- **AND** a click targeting the hidden frame cannot reach Bloom

#### Scenario: Bloom's own outbound embedding is unaffected

- **WHEN** a user opens the OrthoFinder page, which embeds the external OrthoBrowser in an iframe
- **THEN** the embedded OrthoBrowser still loads
- **AND** the header governs only inbound framing of Bloom, not Bloom's outbound embeds

### Requirement: Frame Ancestor Denial

Every HTTP response Caddy serves SHALL carry `Content-Security-Policy: frame-ancestors 'none'`, the standards-track equivalent of `X-Frame-Options: DENY`, so clients that ignore the legacy header still refuse to frame Bloom.

#### Scenario: Anti-framing survives on clients that ignore the legacy header

- **WHEN** a browser that honours `frame-ancestors` but not `X-Frame-Options` is asked to frame a Bloom page
- **THEN** it refuses, because the CSP directive carries the same denial

#### Scenario: The directive stays narrow

- **WHEN** the policy is inspected
- **THEN** it contains `frame-ancestors` only
- **AND** it declares no `script-src`, so it never depends on `'unsafe-inline'` for Next.js hydration scripts

#### Scenario: An upstream's own policy is not weakened

- **WHEN** a covered hostname's upstream emits its own `Content-Security-Policy` and both reach the client
- **THEN** the browser enforces every policy present, admitting only what all of them allow
- **AND** `frame-ancestors 'none'` still applies, while the upstream's own directives remain in force

### Requirement: Cross-Origin Referrer Restriction

Every HTTP response Caddy serves SHALL carry `Referrer-Policy: strict-origin-when-cross-origin`, so URL paths carrying research metadata do not leak to third-party sites.

#### Scenario: Cross-origin navigation reveals only the origin

- **WHEN** a user follows a link from a Bloom page whose URL contains an experiment identifier to a site on another origin
- **THEN** the destination receives only the Bloom origin as the referrer
- **AND** receives no path, query string, or experiment identifier

#### Scenario: Same-origin navigation retains the full URL

- **WHEN** a user navigates between two pages on the same Bloom origin
- **THEN** the full referrer URL is sent, preserving in-app behaviour that depends on it

### Requirement: Unused Browser Feature Restriction

Every HTTP response Caddy serves SHALL carry a `Permissions-Policy` denying `camera`, `microphone`, and `geolocation`, which Bloom does not use.

#### Scenario: Unused features are denied

- **WHEN** script on a Bloom page attempts to access the camera, microphone, or geolocation
- **THEN** the browser denies the request regardless of any user permission previously granted to the origin

#### Scenario: Fullscreen remains available

- **WHEN** a user activates fullscreen on the embedded OrthoBrowser iframe, which is marked `allowFullScreen`
- **THEN** fullscreen still works, because the policy omits `fullscreen` and its default of `self` continues to apply

### Requirement: Uniform Coverage From A Single Declaration

The security headers SHALL be declared once at site level, ahead of the per-host routing matchers, so every hostname Caddy serves inherits them from that one declaration rather than a per-host copy.

#### Scenario: Every route on the main hostname carries the headers

- **WHEN** any path on the main application hostname is served, whether by bloom-web, Kong, the LangChain agent, bloommcp, or the workflows API
- **THEN** the response carries all five security headers
- **AND** they originate from a single declaration rather than a per-route copy

#### Scenario: Responses Caddy generates itself are covered

- **WHEN** Caddy answers with a synthetic status of its own, or with an error because an upstream is unreachable
- **THEN** the response still carries all five headers, because they are applied ahead of the handler chain

#### Scenario: The console hostnames are covered too

- **WHEN** a response is served for the Supabase Studio or MinIO console hostname
- **THEN** it carries the same five headers as the main hostname
- **AND** no separate declaration is needed to achieve that

#### Scenario: Console coverage closes a gap Kong does not

- **WHEN** the reason for covering Supabase Studio is reviewed
- **THEN** it rests on Studio being reachable through Caddy without traversing Kong, so Kong's `basic-auth` on the `dashboard` route does not apply to that path
- **AND** the anti-framing headers are therefore what stops an off-network attacker using an on-network browser to frame an internal console

#### Scenario: Moving the block under a host matcher is a regression

- **WHEN** the declaration is relocated inside a single hostname's `handle` block
- **THEN** that is a regression, because the other hostnames silently lose every header with no error and no failing request
- **AND** the config-shape test fails on the change

#### Scenario: A duplicated header is detected rather than assumed absent

- **WHEN** an upstream emits a header the edge also sets, so the client receives it twice
- **THEN** the duplication is asserted against explicitly, not hidden by a presence-only check
- **AND** any header whose duplicate values differ is recorded, since headers that resolve last-wins would let an upstream silently override the edge
