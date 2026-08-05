## ADDED Requirements

### Requirement: Multi-valued selectors for cyl download

`bloomctl cyl download` SHALL accept multi-valued selectors so a caller can resolve many scans in
one invocation: repeatable `--experiment-id` and `--scan-id`, `--barcodes-file <path|->`,
`--barcodes a,b,c`, `--accession NAME`, and `--species NAME`. `--barcodes-file` and `--barcodes`
SHALL be mutually exclusive with each other.

#### Scenario: Download by a list of barcodes

- **WHEN** the caller runs `cyl download OUT --barcodes-file codes.txt` with a file of 200 barcodes
- **THEN** every scan whose plant QR code appears in the file is downloaded
- **AND** barcodes with no matching plant are reported on stderr without aborting the run

#### Scenario: Barcodes supplied on stdin

- **WHEN** the caller pipes a newline-delimited barcode list and passes `--barcodes-file -`
- **THEN** the list is read from stdin and resolved identically to a file path

#### Scenario: Several experiments in one invocation

- **WHEN** the caller passes `--experiment-id 10 --experiment-id 22`
- **THEN** scans from both experiments are downloaded into the same output directory
- **AND** `scans.csv` contains the rows of both, distinguished by `experiment_id`

### Requirement: Selectors compose as an intersection

Supplied selectors SHALL be combined as a logical AND, and an omitted selector SHALL impose no
constraint. Supplying `--experiment-id` together with `--scan-id` SHALL NOT be an error.

#### Scenario: Species and accession together

- **WHEN** the caller passes `--species soybean --accession Col-0`
- **THEN** only plants that are both soybean and accession `Col-0` are resolved

#### Scenario: Omitted selectors impose no constraint

- **WHEN** the caller passes only `--accession Col-0`
- **THEN** matching plants are resolved across every non-deleted experiment and species

#### Scenario: Selection resolves to nothing

- **WHEN** the selectors match no plants
- **THEN** the command warns that nothing matched and exits non-zero, per #384, so a wrong filter is
  not mistaken for a successful empty download
- **AND** no output directory or header-only `scans.csv` is written

### Requirement: Selection is previewable before download

`cyl download` SHALL support `--dry-run`, which resolves the selection, reports scan count,
experiment count, and estimated image count, and exits without downloading. When a resolved
selection exceeds a configured scan threshold, the command SHALL refuse to download unless `--yes`
is supplied.

#### Scenario: Dry run reports the selection

- **WHEN** the caller passes `--species soybean --dry-run`
- **THEN** the resolved counts are printed and no images are fetched
- **AND** the process exits zero

#### Scenario: Oversized selection is gated

- **WHEN** a resolved selection exceeds the scan threshold and `--yes` is absent
- **THEN** the command exits non-zero with the resolved counts and instructions to re-run with
  `--yes` or narrow the selection
- **AND** no images are fetched

#### Scenario: Oversized selection proceeds when confirmed

- **WHEN** the same selection is re-run with `--yes`
- **THEN** the download proceeds without prompting

### Requirement: Selection capabilities are shared read models, not CLI-only logic

Any database view or function added to support selection SHALL be a shared read model callable by
any authenticated client over the standard API — the same shape as `cyl_plant_search_query` (#516).
It SHALL run with the caller's own permissions and SHALL carry role grants covering both the web and
CLI callers. Selection logic that only the CLI can execute SHALL NOT be introduced.

#### Scenario: A new selection capability is reachable from the web

- **WHEN** a database view or function is added for selection
- **THEN** the web application can call it directly to resolve the same selection
- **AND** enabling web bulk download using it requires no further backend work

#### Scenario: Permissions are enforced server-side

- **WHEN** any client resolves a selection
- **THEN** the query runs with that caller's own permissions
- **AND** rows the caller may not read are absent regardless of which client asked

#### Scenario: Caps are enforced server-side, not per client

- **WHEN** a filter list exceeds the server-side cap
- **THEN** the server rejects it for every client identically
- **AND** neither client is relied upon to enforce the limit

#### Scenario: Client-side filtering is rejected

- **WHEN** a proposed implementation resolves a selection by fetching rows and filtering them inside
  the CLI
- **THEN** it does not satisfy this requirement, because the web cannot reuse it

### Requirement: Selection resolution is paged and never silently truncated

Selection SHALL be resolved through the existing `cyl_plant_search_query` RPC. When a result set
exceeds the RPC's page cap the CLI SHALL page until the selection is complete. If a server-side
filter or row cap prevents completion, the CLI SHALL emit a warning on stderr naming the cap, and
SHALL NOT present a partial selection as complete.

#### Scenario: Selection larger than one page

- **WHEN** a selection resolves to more rows than the RPC returns in one page
- **THEN** the CLI pages until exhausted and downloads the full selection

#### Scenario: Filter list exceeds the server cap

- **WHEN** the caller supplies more barcodes than the RPC's per-field filter cap allows
- **THEN** the command fails with a clear message naming the cap and the supplied count
- **AND** no partial download occurs

#### Scenario: Machine output stays clean

- **WHEN** a cap warning is emitted during a run using `--output`
- **THEN** the warning is written to stderr and stdout carries only machine-readable output
