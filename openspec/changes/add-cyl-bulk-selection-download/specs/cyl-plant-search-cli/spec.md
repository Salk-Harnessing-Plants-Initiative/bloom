## ADDED Requirements

### Requirement: Multi-field plant search in the CLI

The CLI SHALL provide `bloomctl cyl search`, resolving plants over the existing `cyl_plant_search`
view via `cyl_plant_search_query`. It SHALL accept `--accession NAME`, `--species NAME`,
`--experiment NAME`, and `--experiment-id`, combined as an intersection. Because the accession name
is the field users refer to as the PI, `--accession` SHALL satisfy PI-oriented search without a new
schema field.

#### Scenario: Search by accession

- **WHEN** the caller runs `cyl search --accession Col-0`
- **THEN** matching plants are listed with barcode, accession, species, and experiment

#### Scenario: Search fields intersect

- **WHEN** the caller runs `cyl search --species soybean --accession Col-0`
- **THEN** only plants matching both are returned

#### Scenario: Soft-deleted experiments are excluded

- **WHEN** a matching plant belongs to a soft-deleted experiment
- **THEN** it is absent from the results, matching the view's existing behaviour

### Requirement: Batch barcode lookup

`cyl search` SHALL accept a batch of barcodes in one invocation via `--barcodes-file <path|->` or
`--barcodes a,b,c`, resolving them in a single query rather than one lookup per barcode. Barcodes
that match nothing SHALL be reported distinctly from barcodes that matched.

#### Scenario: Resolve a pasted barcode list

- **WHEN** the caller passes a file of 300 barcodes to `--barcodes-file`
- **THEN** all matching plants are returned from one query

#### Scenario: Unmatched barcodes are surfaced

- **WHEN** some supplied barcodes match no plant
- **THEN** the command reports which barcodes were not found
- **AND** exits zero when at least one barcode matched

#### Scenario: Batch exceeding the server filter cap

- **WHEN** the supplied barcode count exceeds the RPC's per-field filter cap
- **THEN** the command exits non-zero naming the cap and the supplied count

### Requirement: Any field can be answered from any filter

`cyl search` SHALL support `--show plants|experiments|accessions|species`, defaulting to `plants`.
Given any filter, it SHALL be able to return the distinct values of any of the other fields, since
`cyl_plant_search` carries barcode, accession, species, experiment and wave on every row.

#### Scenario: Barcode to its context

- **WHEN** the caller supplies barcodes with the default `--show plants`
- **THEN** each plant row carries its accession, species, and experiment

#### Scenario: Accession to the experiments it appears in

- **WHEN** the caller runs `cyl search --accession Col-0 --show experiments`
- **THEN** each experiment that accession was scanned in is listed once

#### Scenario: Species to its accessions

- **WHEN** the caller runs `cyl search --species soybean --show accessions`
- **THEN** each accession under that species is listed once

#### Scenario: Experiment to its accessions

- **WHEN** the caller runs `cyl search --experiment-id 10 --show accessions`
- **THEN** each accession used in that experiment is listed once

### Requirement: Rollups are computed server-side over the whole match

When `--show` is not `plants`, the distinct values SHALL be computed by the shared query across the
entire matching set, not by de-duplicating a page of plant rows in the client. When `--show plants`
would exceed the server row cap, the command SHALL warn on stderr and name the `--show` value that
answers the question without truncation.

#### Scenario: A broad filter still returns a complete rollup

- **WHEN** the caller runs `--show experiments` on a filter matching far more plants than one page
- **THEN** every matching experiment is listed, not only those in the first page of plant rows

#### Scenario: Truncation is never presented as a complete answer

- **WHEN** `--show plants` matches more rows than the cap allows
- **THEN** a warning naming the cap is written to stderr
- **AND** the warning names the `--show` value that answers the question completely

#### Scenario: Rollup output is machine-readable

- **WHEN** the caller runs `--show accessions --output csv 2>/dev/null`
- **THEN** stdout carries the distinct accession ids and names with no human-facing text

### Requirement: Search output feeds the download path

`cyl search` SHALL support `--output csv|json` and SHALL emit `scan_id` and `qr_code` columns so its
output can be piped into `cyl download` and `cyl batch-download-for-predict` without reshaping.

#### Scenario: Search output drives a download

- **WHEN** the caller pipes `cyl search --accession Col-0 --output csv` into a scan-id extraction and
  passes the result to `cyl download --scan-ids-file -`
- **THEN** exactly the searched plants' scans are downloaded

#### Scenario: Human output by default

- **WHEN** `--output` is omitted
- **THEN** results are rendered as a table on stdout
