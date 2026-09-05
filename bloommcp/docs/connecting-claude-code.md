# Connecting Claude Code to bloommcp

This guide gets Claude Code talking to the hosted bloommcp MCP server, so you can run plant
phenotyping analyses (QC, PCA, clustering, and more) directly from a Claude Code conversation
against Bloom's real data.

## Before you connect: what this token actually grants

**Read this before running any command below.** The token behind either environment is a single
shared credential (`BLOOMMCP_API_KEY`), used by everyone who connects. It is not scoped to you, your
lab, or your experiments:

- **Read access spans virtually every table in the scientific database by default** — cylinder
  scan traits, scRNA-seq data, gene candidates, experiment metadata, and anything else in the
  `public` schema, including tables created after this guide was written. There is no
  per-user/per-lab/per-experiment filtering on this default: if the schema-wide grant covers a
  table, everyone holding the shared token can read every row in it.
- **Row-level security can, and in at least one confirmed case does, narrow that default.** A
  table's own security policy can restrict it to a different role than the one this token uses —
  `gene_patents` (patent status, government ID) is a confirmed example: it is not actually readable
  through this token today, despite the general rule above. Don't treat "virtually every table" as
  an unconditional guarantee of full database access — it's the default, not an absolute.
- **Write access is confined to one Storage bucket, plus one narrow database-table exception.** In
  the `bloommcp-data` bucket, insert, update, and delete are all possible (delete only for
  previously-uploaded analysis outputs, never your raw input files). The one exception: this token
  can also insert and update a single database table, `public.bloommcp_usage` — an internal, rolling
  per-caller usage aggregate (identity, first/last seen, request count, last tool called) upserted
  once per tool call for operational tracking. It holds no scientific data. This token cannot write
  to any other database table.
- Verifying who ran a request (a separate, prior change) does **not** change the read/write scope
  above — it's an audit trail, not a data-access restriction. Every request still reads/writes
  scientific data with the same shared permissions regardless of who's identified as making it; the
  `bloommcp_usage` write above is what records that identity, not a new door into anything else.

If you'd rather not connect to the shared server at all — so no experiment data leaves your
machine, and you have no access to Bloom's live data — skip to [No shared server? Run bloommcp
fully locally](#no-shared-server-run-bloommcp-fully-locally) below.

## Prerequisite: Salk wifi or VPN

bloommcp is only reachable from Salk's network. Connect to Salk wifi, or Salk VPN if you're
off-campus, before running the commands below. If a connect command below hangs or times out
instead of returning an error, this is the first thing to check — an unreachable host looks like a
hang, not a clean failure.

## Connecting to production

```bash
claude mcp add --transport http bloommcp-prod https://bloom.salk.edu/bloommcp/mcp --header "Authorization: Bearer <token>"
```

## Connecting to staging

```bash
claude mcp add --transport http bloommcp-staging https://staging.bloom.salk.edu:8443/bloommcp/mcp --header "Authorization: Bearer <token>"
```

**The `:8443` is not optional — don't drop it.** Staging and production share the same host
(`*.bloom.salk.edu`) and the same TLS certificate. If you connect to
`https://staging.bloom.salk.edu` on the default port 443 (i.e., without `:8443`), the TLS handshake
still succeeds — the certificate covers that hostname too — but the request actually lands on
**production's** server, which has no route for the staging hostname and silently returns a blank
`200` response instead of an error. You won't get a clean failure telling you the port is wrong; you
just won't be talking to staging. Always include `:8443` for the staging endpoint.

**Replace `<token>` with your actual key, angle brackets and all removed** — pasting the placeholder
literally (`Bearer <token>`) produces a header that looks fine and `claude mcp add` will still accept
it, but every request then fails with no obvious error pointing at the token.

## Where to get `<token>`

`<TODO: name the BLOOMMCP_API_KEY contact/process — not yet determined at the time this guide was
written. There is no self-service lookup for this token today.>`

## No shared server? Run bloommcp fully locally

If you'd rather not touch the shared server — for example, to work entirely offline, or to avoid
the access-scope trade-offs above — bloommcp supports a fully-local mode: your own input files in,
your own output files out, and no experiment data leaves your machine. See
[storage-backends.md](storage-backends.md) for how to set `BLOOM_STORAGE_BACKEND=local` and run
bloommcp this way from Claude Code or Claude Desktop.

## One-off analysis without registering data

If you just want to QC a local CSV you have open in Claude Code — not data that lives in, or
should be added to, Bloom's database — `qc_clean` accepts the file's text directly via
`csv_content` instead of a registered `experiment` name:

```
qc_clean(csv_content="<the CSV file's text>")
```

This never touches the shared Storage/DB described above: nothing is uploaded, nothing is
registered, and none of the read/write access-scope tradeoffs on this page apply — the content
is parsed in memory for the one call and then discarded. See `csv_content`'s own parameter
description (surfaced in the tool's schema) for exactly what is and isn't preserved — in short,
there's no run to look up afterward, so treat this as a one-off check, not a registered
experiment.

### Carrying an inline clean forward

Because nothing is persisted, there is no cleaned version for a later tool to resolve — the
`based_on_version` chaining a registered experiment gets is not available here. To feed the
cleaned table into another analysis, ask `qc_clean` to hand it back and pass it on yourself:

```
result = qc_clean(csv_content="<the CSV file's text>", return_cleaned_csv=true)
# result.cleaned_csv now holds the cleaned table as text.
```

**Which tools accept that text is still growing.** `qc_clean` is the only tool with a
`csv_content` parameter today; `pca_analysis`, `clustering`, `umap_analysis`,
`descriptive_stats` and `remove_outliers` gain one in the follow-up work tracked by
[#582](https://github.com/Salk-Harnessing-Plants-Initiative/bloom/issues/582). Until then
`cleaned_csv` is something you hold onto or write to a file — passing it to another tool will
fail schema validation, because the parameter does not exist yet. Check a tool's schema for
`csv_content` before reaching for it.

The chaining is yours, not the server's: bloommcp keeps no copy of `cleaned_csv` and records no
link between the two calls. `cleaned_csv_sha256` is there so you can prove to yourself that a
later call analyzed the table this one produced. `return_cleaned_csv` is off by default (the
table can be large) and is rejected with a registered `experiment`, which already persists the
cleaned CSV as a downloadable run artifact.

Two things about the returned text specifically. It is your own data echoed verbatim — bloommcp
does not escape or sanitize cell values, so if you save it and open it in a spreadsheet, a cell
that started with `=`, `+`, `-` or `@` in your input is still a formula in the output. And the
no-NaN guarantee `qc_clean` reports covers the trait columns it kept, not the whole table:
identifier and metadata columns can legitimately still be blank, so don't read an empty cell
outside `kept_trait_columns` as a cleaning failure.

### One caveat worth stating plainly

`csv_content` is never written anywhere and never logged — but that guarantee is about
bloommcp's normal operation. Raising the server's log level to `DEBUG` makes the MCP transport
log whole request bodies, inline CSV included, into the container's logs. Don't run a shared
bloommcp at `DEBUG` while callers are passing data they chose not to register.

## Claude Desktop / Claude Enterprise

**Not yet written.** Claude Desktop and Claude Enterprise custom connectors work differently from
Claude Code — they connect from Anthropic's own cloud infrastructure rather than from your device,
which raises separate questions (network allowlisting, authentication mode) that haven't been
decided yet. Track that discussion at
[#522](https://github.com/Salk-Harnessing-Plants-Initiative/bloom/issues/522); this section will be
written once it resolves.
