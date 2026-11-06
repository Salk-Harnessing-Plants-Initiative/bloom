# Connecting Claude to bloommcp

bloommcp lets Claude run plant phenotyping analyses (QC, PCA, clustering, and more) against
Bloom's data from a conversation. You reach it through the Bloom connector that Salk provides for
Claude.

## What you need

- **A Salk account.** The connector signs you in with Salk.
- **A Bloom account with the same Salk email.** If you don't have one, sign up on the Bloom
  website first. Without one, the connector refuses with "No Bloom account for <your email>.
  Create one at <Bloom's address>, or reach out to the Bloom team." Claude may instead show
  only that it couldn't connect to Bloom; the usual cause is the same. If you already have
  an account and see either, reach out to the Bloom team.

## Connecting

1. In claude.ai or Claude Desktop, open the connectors settings and add the **Bloom** connector.
   Salk's AIRC team publishes it; there is also **Bloom staging** for testing. If you can't find
   either, ask AIRC or Salk IT for it.
2. Sign in with Salk when asked.
3. Start a conversation and ask for an analysis; Claude calls bloommcp's tools.

Claude Code signed in with the same Claude account can use connectors added there; run `/mcp` in
Claude Code to check that Bloom is listed.

Within a minute of a Bloom account being removed, the connector stops working for that person.

bloommcp is not served on the Bloom website (`bloom.salk.edu/bloommcp`); the connector is the only
way in from outside.

## What the connector can see and change

Signing in tells bloommcp who you are, and your usage is recorded under your Bloom account. It
does not narrow what bloommcp can do: every request reads and writes with the same shared
permissions (the `bloom_agent` role), whoever made it.

- **Reads cover virtually every table in the scientific database** — cylinder scan traits,
  scRNA-seq data, gene candidates, experiment metadata, and anything else in the `public` schema.
  There is no per-user, per-lab or per-experiment filtering.
- **Row-level security can narrow that.** `gene_patents` is one confirmed table bloommcp cannot
  read, because its policy targets a different role.
- **Writes are confined to the `bloommcp-data` Storage bucket**, plus `public.bloommcp_usage`, an
  internal per-person usage count with no scientific data. In the bucket, outputs can be added,
  updated and deleted; your raw input files are never deleted.

If you'd rather no experiment data leave your machine, run bloommcp locally instead (next section).

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

## Retired tools and what replaced them

If a call fails with an unknown-tool error, the tool was probably retired into a more
capable one. Current retirements:

| Retired tool | Use instead |
| --- | --- |
| `sleap_roots_plot_heritability_bar(filename=X, threshold=T)` | `sleap_roots_heritability_analysis(experiment=X, threshold=T, include_plots=true, plots=["create_heritability_plot"])` |
| `sleap_roots_plot_variance_decomposition(filename=X)` | `sleap_roots_heritability_analysis(experiment=X, include_plots=true, plots=["create_variance_decomposition_plot"])` |

`heritability_analysis` ([#462](https://github.com/Salk-Harnessing-Plants-Initiative/bloom/issues/462))
is not a drop-in rename, and the differences are the point of the change:

- **It returns the numbers.** The retired tools computed per-trait H² internally and then
  threw it away, handing back a PNG link and one aggregate count. You now get every
  trait's H², variance components, genotype and observation counts, and model type as
  data you can query — with the figures as an optional extra from the same call.
- **It requires a cleaned experiment.** Run `sleap_roots_qc_clean` first. The retired
  tools fitted a mixed model on raw data, where the delegate's own per-trait `dropna()`
  silently changed how many samples each trait was scored on, with no signal to you.
- **It persists a versioned run.** You get object-key links to `heritability.csv` and
  `heritability_result.json` with provenance, discoverable later through
  `core_list_existing_analyses`, instead of a loose PNG at a static URL.
- **One call, both figures.** Asking for both plots computes heritability once, so the
  two charts and the numbers beside them can't come from separate computations.

One thing to know when reading a wide experiment: the bar plot orders traits by H²
descending, while `per_trait` and the persisted table keep the experiment's own trait
order. Same numbers, different slice — so the inline top-50 of a truncated result is not
the first plotted page.
