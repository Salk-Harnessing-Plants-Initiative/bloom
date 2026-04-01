# EPIC: Automated Root Trait Extraction Pipeline Integration with Bloom

## Summary

Integrate the sleap-roots trait extraction pipeline with Bloom, enabling automated pipeline execution triggered from the Bloom ecosystem, with full metadata preservation, versioning, and result synchronization.

## Background

Currently, the sleap-roots pipeline runs manually via CLI commands and Docker containers on a local workstation. Scientists request processing, and results are manually uploaded to Box and shared via email. This process is:

- **Manual**: Requires human intervention at every step
- **Error-prone**: No automated validation or tracking
- **Not scalable**: One-at-a-time processing
- **Disconnected**: Results not linked back to source data in Bloom

## Goals

1. **Automated Triggering**: Scientists can trigger pipeline runs from Bloom (UI or API)
2. **GPU Cluster Execution**: Pipeline runs on RunAI GPU cluster via Argo Workflows
3. **Full Provenance**: Every run captures metadata for reproducibility
4. **Results Integration**: Traits and predictions sync back to Bloom database
5. **Versioning**: Support re-runs with different models, preserve history
6. **Downstream Analysis**: Integrate QC, heritability analysis, and future GWAS
7. **Visualization**: View predictions overlaid on images in Bloom UI

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────────────────┐
│                           Local Server                                   │
│  ┌─────────────┐    ┌─────────────┐    ┌───────────────────────────┐    │
│  │  Supabase   │    │   MinIO     │    │     File Storage          │    │
│  │  (Postgres) │    │ (S3 API)    │    │   /hpi/hpi_dev/bloom/     │    │
│  │             │    │             │    │                           │    │
│  │ - experiments│   │ - uploads   │    │ - experiments/{id}/       │    │
│  │ - scans     │    │             │    │   ├── images/             │    │
│  │ - traits    │    │             │    │   ├── pipeline_outputs/   │    │
│  │ - pipeline_ │    │             │    │   └── predictions/        │    │
│  │   runs      │    │             │    │ - models/                 │    │
│  └──────┬──────┘    └──────┬──────┘    └─────────────┬─────────────┘    │
│         │                  │                         │                   │
└─────────┼──────────────────┼─────────────────────────┼───────────────────┘
          │                  │                         │
          │                  │                         │
    ┌─────┴──────────────────┴─────┐                   │
    │        Bloom Backend         │                   │
    │  - Pipeline trigger API      │                   │
    │  - Status polling            │                   │
    │  - Results ingestion         │                   │
    │  - Box sync                  │                   │
    └─────────────┬────────────────┘                   │
                  │                                    │
                  │ Argo Submit                        │ hostPath mount
                  ▼                                    ▼
    ┌─────────────────────────────────────────────────────────────────┐
    │                      RunAI GPU Cluster                           │
    │  ┌───────────────────────────────────────────────────────────┐  │
    │  │                    Argo Workflows                          │  │
    │  │                                                            │  │
    │  │  ┌─────────────┐   ┌─────────────┐   ┌─────────────────┐  │  │
    │  │  │   models-   │──▶│  predictor  │──▶│ trait-extractor │  │  │
    │  │  │  downloader │   │   (GPU)     │   │                 │  │  │
    │  │  └─────────────┘   └─────────────┘   └────────┬────────┘  │  │
    │  │                                               │           │  │
    │  │                                               ▼           │  │
    │  │                                      ┌─────────────────┐  │  │
    │  │                                      │  results-sync   │  │  │
    │  │                                      │  (to Supabase)  │  │  │
    │  │                                      └─────────────────┘  │  │
    │  └───────────────────────────────────────────────────────────┘  │
    └─────────────────────────────────────────────────────────────────┘
```

## Issues in this EPIC

| Issue | Title | Priority | Dependencies |
|-------|-------|----------|--------------|
| #1 | Infrastructure - Local Storage & RunAI Mount | P0 | None (blocking) |
| #2 | Pipeline Trigger - Manual Execution via Bloom | P0 | #1 |
| #3 | Metadata & Provenance - Pipeline Run Tracking | P0 | #1 |
| #4 | Results Sync - Traits & Predictions to Bloom | P0 | #2, #3 |
| #5 | Downstream Analysis - sleap-roots-analyze Integration | P1 | #4 |
| #6 | UI/UX - Prediction Viewer & Status Dashboard | P1 | #4 |

## Success Metrics

- [ ] Pipeline can be triggered from Bloom in < 5 clicks
- [ ] End-to-end processing time visible to scientists
- [ ] 100% of pipeline runs have full provenance metadata
- [ ] Traits queryable in Bloom within 5 minutes of pipeline completion
- [ ] Predictions viewable in Bloom UI
- [ ] Box backup automatic with link in Bloom

## Technical References

- **Argo Server**: `gpu-master:8888` (HTTP mode)
- **Namespace**: `runai-tye-lab`
- **Storage Mount**: `/hpi/hpi_dev/`
- **Existing Pipelines**:
  - [sleap-roots-pipeline](https://github.com/talmolab/sleap-roots-pipeline)
  - [gapit3-gwas-pipeline](https://github.com/salk-harnessing-plants-initiative/gapit3-gwas-pipeline) (metadata patterns)
  - [sleap-roots-analyze](https://github.com/talmolab/sleap-roots-analyze) (downstream analysis)

## Labels

`epic`, `pipeline`, `automation`, `infrastructure`

---

## Related Documents

- [Pipeline Trigger API Design](./issue-2-pipeline-trigger.md)
- [Metadata Schema](./issue-3-metadata-schema.md)
- [Database Schema Changes](./issue-4-schema-changes.md)
