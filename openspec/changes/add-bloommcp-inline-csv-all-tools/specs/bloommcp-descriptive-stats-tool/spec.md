## ADDED Requirements

### Requirement: descriptive_stats Accepts Inline Content With No Persistence

`descriptive_stats` SHALL accept `csv_content` as the mutually exclusive alternative to
`experiment` (exactly one required). On the inline path it SHALL skip the `ExperimentReader` port
entirely and return the same per-trait statistics, with no run created or committed, `run_ref` /
`version_dir` / `manifest_path` `None`, `outputs` and `output_links` empty, and `input_sha256`
populated. `version` and `user_label` SHALL be rejected.

Because this tool's statistics table is already returned in the response on the registered path,
the inline response SHALL differ only in identity, `source`, `input_sha256`, and the
persistence-linked fields.

`descriptive_stats` SHALL keep its existing per-trait non-finite policy on the inline path: a
non-finite trait is routed to `failed_traits` rather than failing the call. It SHALL NOT gain the
all-or-nothing finiteness guard the other consumers use, because its documented design is
deliberately the opposite — one bad trait must not block hundreds of healthy ones.

#### Scenario: Inline descriptive statistics match the registered path

- **WHEN** `descriptive_stats` is called with a cleaned fixture's text as `csv_content` and with
  that fixture as a registered cleaned experiment
- **THEN** the per-trait statistics are identical

#### Scenario: Inline descriptive statistics persist nothing

- **WHEN** `descriptive_stats` is called with `csv_content`
- **THEN** a `ResultStore` spy records zero `create_run` and zero `commit` calls, and an
  `ExperimentReader` spy records zero `load_experiment` calls

#### Scenario: A non-finite trait is reported, not fatal, on the inline path too

- **WHEN** `descriptive_stats` is called with `csv_content` containing one non-finite trait
  alongside several finite ones
- **THEN** the finite traits are reported normally and the non-finite one appears in
  `failed_traits` — the call does not raise, matching the registered path exactly

#### Scenario: Version pins and labels are rejected on the inline path

- **WHEN** `descriptive_stats` is called with `csv_content` and either `version` or `user_label`
- **THEN** it raises `BloomMCPError(code="invalid_input")` naming the offending parameter
