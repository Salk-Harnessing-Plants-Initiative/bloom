## ADDED Requirements

### Requirement: Accession sample counts report distinct barcodes

`cyl_accession_sample_counts` SHALL report the number of **distinct barcodes** per accession per
species, using `count(DISTINCT qr_code)` rather than `count(*)` over `cyl_plants`. Plant rows with a
NULL `qr_code` SHALL NOT contribute to the count.

#### Scenario: One barcode appearing in several waves

- **WHEN** an accession has a barcode recorded in three waves, producing three plant rows
- **THEN** it contributes 1 to the reported count, not 3

#### Scenario: Plants without a barcode

- **WHEN** an accession has plant rows whose `qr_code` is NULL
- **THEN** those rows contribute 0 to the reported count

#### Scenario: Distinct barcodes in one wave

- **WHEN** an accession has 12 distinct barcodes within a single wave
- **THEN** the reported count is 12

#### Scenario: Grouping is unchanged

- **WHEN** an accession appears under more than one species
- **THEN** it is still reported as one row per species, each counting that species' distinct barcodes

### Requirement: The counts view stays a shared read model

The redefined view SHALL keep its `security_invoker` setting and its existing grants to
`authenticated`, `bloom_user`, `bloom_admin`, and `bloom_agent`, with `anon` revoked. The change
SHALL be a view redefinition only — no table, column, or constraint is altered.

#### Scenario: Access is unchanged

- **WHEN** the web app or the CLI reads the view after the change
- **THEN** the same roles can read it with the same permissions
- **AND** the only client-side change required is the renamed column

#### Scenario: Row-level security still applies

- **WHEN** a caller reads the view
- **THEN** the query runs with that caller's own permissions
- **AND** soft-deleted experiments remain excluded as before

### Requirement: The renamed column makes the change self-announcing

The view's output column SHALL be renamed `plant_count` → `barcode_count`, and the CLI column header
SHALL change from `Plants` to `Barcodes`, so a figure produced before the change cannot be silently
compared with one produced after it.

#### Scenario: The column carries its new name

- **WHEN** a caller reads the view after the change
- **THEN** the count is exposed as `barcode_count`
- **AND** `plant_count` is no longer present

#### Scenario: The CLI header matches the unit

- **WHEN** the caller runs `cyl accessions sample-counts`
- **THEN** the count column is headed `Barcodes`

### Requirement: The count's meaning is stated where it is shown

`bloomctl cyl accessions sample-counts` SHALL make clear in its help text that the reported figure is
distinct barcodes, so the number is not mistaken for a plant-row or replicate count.

#### Scenario: Help text states the unit

- **WHEN** the caller runs `cyl accessions sample-counts --help`
- **THEN** the description states that the count is distinct barcodes per accession per species
