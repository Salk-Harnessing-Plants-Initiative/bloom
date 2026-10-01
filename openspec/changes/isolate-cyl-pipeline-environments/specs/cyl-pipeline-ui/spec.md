## MODIFIED Requirements

### Requirement: Starting pipeline runs can be switched off per environment
The web app SHALL offer run actions, and the trigger proxy SHALL accept a request, only when the server-side setting `CYL_PIPELINE_TRIGGER_ENABLED` is exactly `true`, read at request time. Otherwise:
- no run action, scan checkbox, "Select all shown" or selection bar is rendered, on any surface;
- `POST /api/cyl/pipeline` responds `503` before reading the session or body, and makes no upstream request;
- the live views (runs list, drill-down, experiment panel) are unchanged.

It is `true` in staging and `false` in prod until prod's own pipeline credential Secret and stage directories are provisioned (bloom#863). The dispatch worker reads the same switch, at start-up, and fails every batch it claims while it is off (see `cyl-pipeline-dispatch`).

#### Scenario: Switched off hides the run actions
- **WHEN** `CYL_PIPELINE_TRIGGER_ENABLED` is unset, `false` or `TRUE`, and a member opens a scan, experiment, accession or drill-down page
- **THEN** no run action is rendered, and the experiment page's runs panel still renders

#### Scenario: Switched off refuses the proxy
- **WHEN** `CYL_PIPELINE_TRIGGER_ENABLED` is not `true` and a same-origin signed-in member posts a valid body
- **THEN** the handler responds `503`, and neither the session nor the body is read and no upstream request is made
