## ADDED Requirements

### Requirement: Images and traits retrievable in one operation

`bloomctl cyl download` SHALL support `--with-traits`, which writes a `traits.csv` alongside
`scans.csv` covering the same resolved scan set. Trait rows SHALL be read through the existing
source-aware trait views and SHALL carry `scan_id` so they join to `scans.csv` without manual
correlation.

#### Scenario: Download images and traits together

- **WHEN** the caller runs `cyl download OUT --experiment-id 10 --with-traits`
- **THEN** `OUT/scans.csv`, `OUT/traits.csv`, and the per-frame images are written
- **AND** every `scan_id` in `traits.csv` is present in `scans.csv`

#### Scenario: Scans without traits are still reported

- **WHEN** part of the resolved selection has no trait rows
- **THEN** those scans appear in `scans.csv` with no corresponding `traits.csv` rows
- **AND** the command reports how many scans lacked traits on stderr

### Requirement: Traits retrievable without fetching images

`cyl download` SHALL support `--traits-only`, which writes `scans.csv` and `traits.csv` and skips
image download. `--traits-only` and `--meta-only` SHALL be mutually exclusive.

#### Scenario: Pull traits for a selection without images

- **WHEN** the caller runs `cyl download OUT --species soybean --traits-only`
- **THEN** `scans.csv` and `traits.csv` are written and no image files are fetched

#### Scenario: Conflicting output modes are rejected

- **WHEN** the caller passes both `--traits-only` and `--meta-only`
- **THEN** the command exits non-zero with a message naming the conflicting flags

### Requirement: Selection by trait predicate

The CLI SHALL provide `cyl traits select`, resolving a set by trait value using a repeatable
`--trait NAME[:MIN][:MAX]`. Either bound MAY be omitted to leave that side open. Multiple predicates
SHALL intersect. `--min` / `--max` SHALL be accepted as an alternative for a single predicate and
SHALL be rejected when more than one `--trait` is supplied.

#### Scenario: Select by a single trait range

- **WHEN** the caller runs `cyl traits select --trait primary_root_length:100:400`
- **THEN** only records whose value for that trait falls within the bounds are emitted

#### Scenario: Open-ended bounds

- **WHEN** the caller supplies `--trait total_length:50:`
- **THEN** records with a value of at least 50 are emitted with no upper bound applied

#### Scenario: Multiple trait predicates intersect

- **WHEN** two `--trait` predicates are supplied
- **THEN** only records satisfying both are emitted

#### Scenario: Ambiguous bound flags are rejected

- **WHEN** the caller supplies two `--trait` flags together with `--min`
- **THEN** the command exits non-zero explaining that `--min` / `--max` apply to a single predicate

#### Scenario: Ambiguous trait name fails loudly

- **WHEN** `--trait NAME` matches trait names under more than one source
- **THEN** the command exits non-zero listing the candidate sources
- **AND** nothing is emitted

### Requirement: Trait selection is restricted by the shared selectors

`cyl traits select` SHALL accept the same selectors as `cyl search` and `cyl download`
(`--barcodes-file`, `--barcodes`, `--accession`, `--species`, `--experiment-id`), applying them as a
pre-filter that the trait predicate is evaluated within. Selectors and predicates SHALL be resolved
in a single server-side query, not by intersecting two result sets in the client.

#### Scenario: Shortlist within a supplied barcode list

- **WHEN** the caller runs `cyl traits select --barcodes-file my200.txt --trait primary_root_length:100:400`
- **THEN** only records belonging to barcodes in the file and satisfying the predicate are emitted
- **AND** barcodes in the file that do not satisfy the predicate are absent

#### Scenario: Selectors stack with the predicate

- **WHEN** `--species soybean` and a trait predicate are supplied together
- **THEN** only soybean records satisfying the predicate are emitted

#### Scenario: Resolution happens server-side

- **WHEN** a barcode list and a trait predicate are supplied together
- **THEN** both are sent to the shared query in one request
- **AND** the client does not fetch an unfiltered set to narrow locally

### Requirement: Result granularity is selectable

`cyl traits select` SHALL support `--grain scan|barcode`, defaulting to `scan`. At `scan` grain it
SHALL emit one row per matching scan including `qr_code`, `scan_id`, and the trait value. At
`barcode` grain it SHALL emit one row per matching barcode including `qr_code`, `scans_matched`, and
`scans_total`. At `barcode` grain, `--match any|all` SHALL decide whether one qualifying scan
suffices (`any`, the default) or every scan must qualify (`all`).

#### Scenario: Scan grain feeds a download

- **WHEN** the caller runs with the default grain and passes the output to
  `cyl download --scan-ids-file -`
- **THEN** exactly the matching scans are downloaded

#### Scenario: Barcode grain shortlists plants

- **WHEN** the caller runs `--grain barcode`
- **THEN** each matching barcode appears once
- **AND** `scans_matched` and `scans_total` distinguish a barcode matching 1 of 12 scans from one
  matching 12 of 12

#### Scenario: Match rule all

- **WHEN** the caller runs `--grain barcode --match all`
- **THEN** a barcode with any non-qualifying scan is excluded

#### Scenario: Match rule does not apply at scan grain

- **WHEN** `--match` is supplied with `--grain scan`
- **THEN** the command exits non-zero explaining that `--match` applies only to `--grain barcode`

### Requirement: Trait output is machine-readable

Trait-bearing commands SHALL support `--output csv|json`, and any menu or progress text SHALL be
written to stderr so stdout carries only machine-readable output.

#### Scenario: JSON output is clean

- **WHEN** the caller runs `cyl traits select --trait X --output json 2>/dev/null`
- **THEN** stdout parses as JSON with no interleaved human-facing text

### Requirement: Observed trait ranges are discoverable before use

The CLI SHALL provide `cyl traits list`, reporting for each trait its name, the number of scans
carrying it, and the observed **minimum and maximum** value. It SHALL accept the same selectors as
`cyl search`, so the reported range describes the filtered set rather than the whole database. The
aggregate SHALL be computed server-side across the entire matching set, never by aggregating a page
of values in the client.

#### Scenario: Ranges are reported for a filtered set

- **WHEN** the caller runs `cyl traits list --experiment-id 10`
- **THEN** each trait measured in that experiment is listed with its scan count, minimum and maximum
- **AND** the values reflect that experiment, not the whole database

#### Scenario: Fractional values are reported as stored

- **WHEN** a trait's values are fractional
- **THEN** the reported minimum and maximum preserve them and are not rounded to integers

#### Scenario: Range covers the whole match, not one page

- **WHEN** a filter matches more trait rows than the server returns in one page
- **THEN** the reported minimum and maximum are computed across every matching row

#### Scenario: A name under several sources is not merged

- **WHEN** a trait name exists under more than one source
- **THEN** each source is reported as its own row with its own range
- **AND** values from different sources are never combined into one range

#### Scenario: Output is machine-readable

- **WHEN** the caller runs `cyl traits list --experiment-id 10 --output csv 2>/dev/null`
- **THEN** stdout carries the trait rows with no human-facing text

### Requirement: Download can be restricted by trait range

`cyl download` SHALL accept the same repeatable `--trait NAME[:MIN][:MAX]` predicate as
`cyl traits select`, restricting the download to scans whose trait value falls in range. It SHALL use
the same shared query, not a second implementation. Trait selection SHALL be independent of trait
output: `--trait` decides which scans are downloaded, while `--with-traits` and `--traits-only`
decide which files are written.

#### Scenario: Download restricted by a trait range

- **WHEN** the caller runs `cyl download OUT --experiment-id 10 --trait primary_root_length:12.4:88.1`
- **THEN** only scans in that experiment whose value falls in the range are downloaded

#### Scenario: Trait selection and trait output are independent

- **WHEN** the caller supplies `--trait` without `--with-traits`
- **THEN** the selection is restricted by the trait and only images and `scans.csv` are written

#### Scenario: Trait output without trait selection

- **WHEN** the caller supplies `--with-traits` without `--trait`
- **THEN** every scan in the selection is downloaded and `traits.csv` covers all of them

#### Scenario: Predicate behaves identically across commands

- **WHEN** the same `--trait` predicate and selectors are given to `cyl traits select` and to
  `cyl download`
- **THEN** both resolve the same scan set
