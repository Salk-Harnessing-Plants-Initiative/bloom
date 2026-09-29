## ADDED Requirements

### Requirement: cross_experiment_correlations Resolves Each Side Independently

`cross_experiment_correlations` SHALL accept `csv_content_1` and `csv_content_2` as the mutually
exclusive alternatives to `experiment_1` and `experiment_2` respectively, with the exactly-one-of
rule enforced **per side**, and each side's error naming that side. A mixed call supplying a
registered experiment for one side and inline content for the other SHALL be valid in **both**
directions — argument order is documented as significant for this tool, so one direction does not
cover the other by symmetry.

If **either** side is inline, the call SHALL be fully ephemeral: no run is created or committed,
because a persisted run whose composite storage key and `based_on_version` name one resolvable
version and one unregistered blob would record a lineage that cannot be resolved.

The response SHALL carry `input_sha256_1` and `input_sha256_2`, each `None` for a side supplied
as a registered experiment, and `experiment_1` / `experiment_2` / `source_1` / `source_2` SHALL
be `None` for an inline side rather than carrying a placeholder.

#### Scenario: A mixed registered/inline call succeeds and persists nothing

- **WHEN** the tool is called with `experiment_1` naming a registered cleaned experiment and
  `csv_content_2` carrying a cleaned table
- **THEN** the correlation results are returned, a `ResultStore` spy records zero `create_run`
  and zero `commit` calls, `input_sha256_1` is `None`, and `input_sha256_2` is the digest of the
  supplied bytes

#### Scenario: The mirror direction behaves the same way

- **WHEN** the tool is called with `csv_content_1` and `experiment_2`
- **THEN** it succeeds, persists nothing, `input_sha256_1` is the digest, and `input_sha256_2` is
  `None`

#### Scenario: Both sides inline reproduces the both-registered result

- **WHEN** the tool is called with both sides' cleaned fixtures as `csv_content_1` /
  `csv_content_2`, and separately with both as registered cleaned experiments
- **THEN** the correlations and significance results are identical

#### Scenario: Both inputs supplied for one side is rejected, naming that side

- **WHEN** the tool is called with both `experiment_1` and `csv_content_1` supplied, regardless
  of what side 2 carries
- **THEN** it raises `BloomMCPError(code="invalid_input")` naming side 1

#### Scenario: Neither input supplied for one side is rejected, naming that side

- **WHEN** the tool is called with a valid side 1 and neither `experiment_2` nor `csv_content_2`
- **THEN** it raises `BloomMCPError(code="invalid_input")` naming side 2

### Requirement: cross_experiment_correlations Applies Its Guards Per Side

Each of this tool's existing guards SHALL be applied per side rather than to the call as a whole.
The composite-storage-key guards — path-unsafe names, reserved encoding characters, and dotted
filename stems — exist to protect a storage key that is never built when either side is inline.
They SHALL continue to apply to any side supplied as a registered experiment and SHALL be
skipped for an inline side.

The existing self-correlation rejection SHALL extend to inline content: two inline sides whose
`input_sha256` are equal are the same table, and SHALL be rejected the same way two identical
experiment names are. A **mixed** pair SHALL NOT be checked for self-correlation — there is no
digest for the registered side to compare against — and this SHALL be stated rather than left to
be discovered as an inconsistency.

`version_1` and `version_2` SHALL each be rejected only when **their own side** is inline, so a
mixed call may legitimately pin the registered side's version. `user_label` SHALL be rejected
whenever either side is inline, since no version directory is created.

A non-finite value in a selected trait column on an inline side SHALL raise `invalid_input`
naming that side and the offending columns, rather than the registered path's
`assumption_violated` with an absent identifier interpolated.

#### Scenario: Two identical inline sides are rejected as self-correlation

- **WHEN** the tool is called with `csv_content_1` and `csv_content_2` carrying byte-identical
  content
- **THEN** it raises `BloomMCPError(code="invalid_input")` rejecting self-correlation, the same
  way two identical experiment names are rejected

#### Scenario: A mixed pair carrying the same table is allowed, deliberately

- **WHEN** the tool is called with `experiment_1` naming a registered experiment and
  `csv_content_2` carrying that experiment's exact cleaned bytes
- **THEN** the call succeeds — the undetectable case is documented as allowed rather than
  silently inconsistent with the two-inline-sides rule

#### Scenario: A version pin is rejected only for the side that is inline

- **WHEN** the tool is called with `experiment_1` + `version_1` and `csv_content_2` + `version_2`
- **THEN** it raises `BloomMCPError(code="invalid_input")` naming `version_2`, and the error does
  not name `version_1`

#### Scenario: Composite-key guards still fire for a registered side

- **WHEN** the tool is called with a path-unsafe, reserved-character, or dotted-stem
  `experiment_1` and a valid `csv_content_2`
- **THEN** the corresponding guard still raises, exactly as it does for a both-registered call

#### Scenario: Composite-key guards are skipped for an inline side

- **WHEN** the tool is called with two inline sides whose content would trip no guard
- **THEN** the call succeeds — no name-shaped guard is applied to a side that has no name

#### Scenario: A non-finite inline side names that side

- **WHEN** the tool is called with `csv_content_2` whose selected trait columns contain a
  non-finite value
- **THEN** it raises `BloomMCPError(code="invalid_input")` naming side 2 and the offending
  columns, and the message contains no `'None'` identifier

#### Scenario: The trait-pair product is bounded when either side is inline

- **WHEN** the tool is called with either side inline and a resolved
  `len(trait_cols_1) * len(trait_cols_2)` above the inline trait-pair cap
- **THEN** it raises `BloomMCPError(code="invalid_input")` naming both counts and their product,
  a spy on the correlation delegate records zero calls, and the existing both-registered oracle
  on the widest available fixture still passes unaffected
