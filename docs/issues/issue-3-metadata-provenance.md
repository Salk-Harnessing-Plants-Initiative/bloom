# Issue 3: Metadata & Provenance - Pipeline Run Tracking

**EPIC**: Automated Root Trait Extraction Pipeline Integration
**Priority**: P0
**Dependencies**: Issue #1 (Infrastructure)
**Blocks**: Issue #4 (Results Sync), Issue #5 (Downstream Analysis)

---

## Summary

Implement comprehensive metadata capture and provenance tracking for every pipeline run, enabling reproducibility, debugging, and scientific rigor.

## Background

For scientific reproducibility, every pipeline run must capture:
- **What** was run (models, parameters, code versions)
- **When** it was run (timestamps, duration)
- **Where** it ran (cluster, node, pod)
- **What** it produced (output checksums, file locations)
- **Why** it might have failed (error messages, logs)

This follows patterns established in the [gapit3-gwas-pipeline](https://github.com/salk-harnessing-plants-initiative/gapit3-gwas-pipeline) for metadata preservation.

## Goals

1. Define versioned metadata schema
2. Capture Argo workflow metadata via environment variables
3. Capture container and model version information
4. Store metadata with pipeline runs in Supabase
5. Generate human-readable run summaries
6. Enable querying runs by metadata (e.g., "find all runs using model X")

## Technical Design

### Metadata Schema (v1.0.0)

```json
{
  "schema_version": "1.0.0",

  "pipeline_run": {
    "id": "uuid",
    "experiment_id": "uuid",
    "triggered_by": "user-uuid",
    "triggered_at": "2026-02-11T15:00:00Z"
  },

  "execution": {
    "status": "completed",
    "started_at": "2026-02-11T15:01:00Z",
    "completed_at": "2026-02-11T16:30:00Z",
    "duration_seconds": 5340,
    "error_message": null
  },

  "argo": {
    "workflow_name": "sleap-roots-pipeline-abc123",
    "workflow_uid": "12345678-abcd-efgh-ijkl-mnopqrstuvwx",
    "namespace": "runai-tye-lab",
    "server": "gpu-master:8888"
  },

  "kubernetes": {
    "pod_name": "sleap-roots-pipeline-abc123-predictor-1234567890",
    "node_name": "gpu-node-01",
    "container_id": "docker://abc123..."
  },

  "containers": {
    "models_downloader": {
      "image": "registry.gitlab.com/salk-tm/models-downloader:v1.2.3",
      "digest": "sha256:abc123...",
      "started_at": "2026-02-11T15:01:00Z",
      "completed_at": "2026-02-11T15:05:00Z",
      "exit_code": 0
    },
    "predictor": {
      "image": "registry.gitlab.com/salk-tm/sleap-roots-predict:v2.0.1",
      "digest": "sha256:def456...",
      "gpu_id": "GPU-0",
      "gpu_memory_used_mb": 4096,
      "started_at": "2026-02-11T15:05:00Z",
      "completed_at": "2026-02-11T16:20:00Z",
      "exit_code": 0
    },
    "trait_extractor": {
      "image": "registry.gitlab.com/salk-tm/sleap-roots-traits:v1.5.0",
      "digest": "sha256:ghi789...",
      "started_at": "2026-02-11T16:20:00Z",
      "completed_at": "2026-02-11T16:30:00Z",
      "exit_code": 0
    }
  },

  "models": {
    "primary": {
      "id": "model-uuid",
      "name": "soybean_primary_v3",
      "version": "3.0.0",
      "path": "/models/primary/soybean_primary_v3.h5",
      "checksum": "sha256:abc123..."
    },
    "lateral": {
      "id": "model-uuid",
      "name": "soybean_lateral_v2",
      "version": "2.1.0",
      "path": "/models/lateral/soybean_lateral_v2.h5",
      "checksum": "sha256:def456..."
    },
    "crown": null
  },

  "parameters": {
    "species": "soybean",
    "mode": "cylinder",
    "min_age": 2,
    "max_age": 8,
    "waves": [1, 2, 3],
    "pipeline_class": "DicotPipeline"
  },

  "input": {
    "experiment_name": "2026_Soybean_Drought_Study",
    "scan_count": 450,
    "image_count": 1800,
    "input_path": "/hpi/hpi_dev/bloom/experiments/uuid/images",
    "total_size_bytes": 5368709120,
    "checksum": "sha256:input_manifest_hash..."
  },

  "output": {
    "output_path": "/hpi/hpi_dev/bloom/experiments/uuid/pipeline_outputs/run-uuid",
    "predictions_count": 1800,
    "traits_file": "traits_summary.csv",
    "traits_row_count": 450,
    "total_size_bytes": 2147483648,
    "files": [
      {
        "name": "traits_summary.csv",
        "path": "traits/traits_summary.csv",
        "size_bytes": 1048576,
        "checksum": "sha256:abc123..."
      },
      {
        "name": "predictions.zip",
        "path": "predictions/predictions.zip",
        "size_bytes": 2146435072,
        "checksum": "sha256:def456..."
      }
    ]
  },

  "provenance": {
    "pipeline_repo": "https://github.com/talmolab/sleap-roots-pipeline",
    "pipeline_version": "v1.0.0",
    "pipeline_commit": "abc123def456...",
    "bloom_version": "v2.5.0",
    "captured_at": "2026-02-11T16:30:05Z"
  }
}
```

### Environment Variable Injection

Argo injects metadata via environment variables in the workflow templates:

```yaml
# In WorkflowTemplate containers
env:
# Argo workflow metadata
- name: ARGO_WORKFLOW_NAME
  value: "{{workflow.name}}"
- name: ARGO_WORKFLOW_UID
  value: "{{workflow.uid}}"
- name: ARGO_NAMESPACE
  value: "{{workflow.namespace}}"

# Kubernetes pod metadata
- name: POD_NAME
  valueFrom:
    fieldRef:
      fieldPath: metadata.name
- name: NODE_NAME
  valueFrom:
    fieldRef:
      fieldPath: spec.nodeName

# Container image (set in template)
- name: CONTAINER_IMAGE
  value: "registry.gitlab.com/salk-tm/sleap-roots-predict:v2.0.1"

# Bloom pipeline run ID (passed as parameter)
- name: PIPELINE_RUN_ID
  value: "{{workflow.parameters.pipeline-run-id}}"

# Model parameters (passed as parameters)
- name: SPECIES
  value: "{{workflow.parameters.species}}"
- name: MIN_AGE
  value: "{{workflow.parameters.min-age}}"
- name: MAX_AGE
  value: "{{workflow.parameters.max-age}}"
```

### Metadata Collection in Pipeline Steps

Each pipeline step writes its metadata to a JSON file:

```python
# In each container's entrypoint script

import os
import json
import hashlib
from datetime import datetime

def collect_step_metadata(step_name: str, output_dir: str) -> dict:
    """Collect metadata for a pipeline step."""
    return {
        "step_name": step_name,
        "started_at": datetime.utcnow().isoformat() + "Z",

        # Argo metadata
        "argo": {
            "workflow_name": os.environ.get("ARGO_WORKFLOW_NAME"),
            "workflow_uid": os.environ.get("ARGO_WORKFLOW_UID"),
            "namespace": os.environ.get("ARGO_NAMESPACE"),
        },

        # Kubernetes metadata
        "kubernetes": {
            "pod_name": os.environ.get("POD_NAME"),
            "node_name": os.environ.get("NODE_NAME"),
        },

        # Container metadata
        "container": {
            "image": os.environ.get("CONTAINER_IMAGE"),
        },

        # Pipeline context
        "pipeline_run_id": os.environ.get("PIPELINE_RUN_ID"),
    }


def finalize_step_metadata(metadata: dict, output_dir: str, success: bool, error: str = None):
    """Finalize and save step metadata."""
    metadata["completed_at"] = datetime.utcnow().isoformat() + "Z"
    metadata["status"] = "completed" if success else "failed"
    metadata["error_message"] = error

    # Calculate output checksums
    metadata["outputs"] = []
    for filename in os.listdir(output_dir):
        filepath = os.path.join(output_dir, filename)
        if os.path.isfile(filepath):
            metadata["outputs"].append({
                "name": filename,
                "size_bytes": os.path.getsize(filepath),
                "checksum": compute_file_checksum(filepath),
            })

    # Save metadata
    metadata_path = os.path.join(output_dir, f"{metadata['step_name']}_metadata.json")
    with open(metadata_path, "w") as f:
        json.dump(metadata, f, indent=2)

    return metadata


def compute_file_checksum(filepath: str, algorithm: str = "sha256") -> str:
    """Compute file checksum."""
    h = hashlib.new(algorithm)
    with open(filepath, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return f"{algorithm}:{h.hexdigest()}"
```

### Metadata Aggregation

A final workflow step aggregates all step metadata:

```yaml
# In sleap-roots-pipeline.yaml
- name: aggregate-metadata
  dependencies: [trait-extractor]
  template: aggregate-metadata-template
  arguments:
    parameters:
    - name: pipeline-run-id
      value: "{{workflow.parameters.pipeline-run-id}}"
    - name: output-path
      value: "{{workflow.parameters.output-path}}"
```

```python
# aggregate_metadata.py
import os
import json
import glob
from datetime import datetime

def aggregate_pipeline_metadata(output_path: str, pipeline_run_id: str) -> dict:
    """Aggregate metadata from all pipeline steps."""

    # Collect step metadata files
    step_metadata = {}
    for metadata_file in glob.glob(f"{output_path}/**/*_metadata.json", recursive=True):
        with open(metadata_file) as f:
            step = json.load(f)
            step_metadata[step["step_name"]] = step

    # Build aggregated metadata
    aggregated = {
        "schema_version": "1.0.0",

        "pipeline_run": {
            "id": pipeline_run_id,
        },

        "execution": {
            "status": determine_overall_status(step_metadata),
            "started_at": get_earliest_start(step_metadata),
            "completed_at": datetime.utcnow().isoformat() + "Z",
        },

        "argo": step_metadata.get("predictor", {}).get("argo", {}),
        "kubernetes": step_metadata.get("predictor", {}).get("kubernetes", {}),

        "containers": {
            name: {
                "image": step.get("container", {}).get("image"),
                "started_at": step.get("started_at"),
                "completed_at": step.get("completed_at"),
                "status": step.get("status"),
            }
            for name, step in step_metadata.items()
        },

        "output": aggregate_outputs(step_metadata),

        "provenance": {
            "captured_at": datetime.utcnow().isoformat() + "Z",
        },
    }

    # Calculate duration
    if aggregated["execution"]["started_at"] and aggregated["execution"]["completed_at"]:
        start = datetime.fromisoformat(aggregated["execution"]["started_at"].rstrip("Z"))
        end = datetime.fromisoformat(aggregated["execution"]["completed_at"].rstrip("Z"))
        aggregated["execution"]["duration_seconds"] = int((end - start).total_seconds())

    # Save aggregated metadata
    metadata_path = os.path.join(output_path, "pipeline_metadata.json")
    with open(metadata_path, "w") as f:
        json.dump(aggregated, f, indent=2)

    return aggregated
```

### Bloom Results Sync Metadata Capture

When syncing results to Bloom, capture the full metadata:

```typescript
// In results sync service

async function syncPipelineMetadata(runId: string, outputPath: string): Promise<void> {
  // Read aggregated metadata from pipeline output
  const metadataPath = path.join(outputPath, 'pipeline_metadata.json');
  const metadata = JSON.parse(await fs.readFile(metadataPath, 'utf-8'));

  // Enrich with Bloom-specific info
  const enrichedMetadata = {
    ...metadata,
    pipeline_run: {
      ...metadata.pipeline_run,
      experiment_id: await getExperimentId(runId),
      triggered_by: await getTriggeredBy(runId),
      triggered_at: await getTriggeredAt(runId),
    },
    provenance: {
      ...metadata.provenance,
      bloom_version: process.env.BLOOM_VERSION,
      sync_timestamp: new Date().toISOString(),
    },
  };

  // Store in database
  await supabase
    .from('cyl_pipeline_runs')
    .update({ metadata: enrichedMetadata })
    .eq('id', runId);
}
```

### Model Version Tracking

Track models used in each run:

```sql
-- Model registry table
CREATE TABLE cyl_models (
  id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  name TEXT NOT NULL,
  version TEXT NOT NULL,
  model_type TEXT NOT NULL CHECK (model_type IN ('primary', 'lateral', 'crown')),
  species TEXT NOT NULL,
  mode TEXT NOT NULL CHECK (mode IN ('cylinder', 'plate', 'turface')),
  age_range INT4RANGE,
  file_path TEXT NOT NULL,
  checksum TEXT NOT NULL,
  is_active BOOLEAN DEFAULT true,
  created_at TIMESTAMPTZ DEFAULT NOW(),
  UNIQUE(name, version)
);

-- Link models to pipeline runs
CREATE TABLE cyl_pipeline_run_models (
  pipeline_run_id UUID REFERENCES cyl_pipeline_runs(id) ON DELETE CASCADE,
  model_id UUID REFERENCES cyl_models(id),
  model_type TEXT NOT NULL,
  PRIMARY KEY (pipeline_run_id, model_id)
);
```

### Human-Readable Run Summary

Generate markdown summary for each run:

```python
def generate_run_summary(metadata: dict) -> str:
    """Generate human-readable summary of pipeline run."""
    return f"""
# Pipeline Run Summary

## Overview
- **Run ID**: {metadata['pipeline_run']['id']}
- **Experiment**: {metadata['pipeline_run'].get('experiment_name', 'N/A')}
- **Status**: {metadata['execution']['status']}
- **Duration**: {format_duration(metadata['execution'].get('duration_seconds', 0))}

## Execution Timeline
| Step | Status | Duration |
|------|--------|----------|
| Models Downloader | {metadata['containers'].get('models_downloader', {}).get('status', 'N/A')} | {format_step_duration(metadata['containers'].get('models_downloader', {}))} |
| Predictor | {metadata['containers'].get('predictor', {}).get('status', 'N/A')} | {format_step_duration(metadata['containers'].get('predictor', {}))} |
| Trait Extractor | {metadata['containers'].get('trait_extractor', {}).get('status', 'N/A')} | {format_step_duration(metadata['containers'].get('trait_extractor', {}))} |

## Input Data
- **Scans**: {metadata['input'].get('scan_count', 'N/A')}
- **Images**: {metadata['input'].get('image_count', 'N/A')}
- **Total Size**: {format_bytes(metadata['input'].get('total_size_bytes', 0))}

## Output
- **Predictions**: {metadata['output'].get('predictions_count', 'N/A')}
- **Traits Extracted**: {metadata['output'].get('traits_row_count', 'N/A')}

## Models Used
| Type | Name | Version |
|------|------|---------|
| Primary | {metadata['models'].get('primary', {}).get('name', 'N/A')} | {metadata['models'].get('primary', {}).get('version', 'N/A')} |
| Lateral | {metadata['models'].get('lateral', {}).get('name', 'N/A')} | {metadata['models'].get('lateral', {}).get('version', 'N/A')} |

## Cluster Info
- **Workflow**: {metadata['argo'].get('workflow_name', 'N/A')}
- **Node**: {metadata['kubernetes'].get('node_name', 'N/A')}
- **Namespace**: {metadata['argo'].get('namespace', 'N/A')}

## Provenance
- **Pipeline Version**: {metadata['provenance'].get('pipeline_version', 'N/A')}
- **Commit**: {metadata['provenance'].get('pipeline_commit', 'N/A')[:8] if metadata['provenance'].get('pipeline_commit') else 'N/A'}
- **Captured At**: {metadata['provenance'].get('captured_at', 'N/A')}

---
*Generated by Bloom Pipeline System*
"""
```

---

## Database Schema Changes

```sql
-- Add metadata column to pipeline_runs (if not exists from Issue #2)
ALTER TABLE cyl_pipeline_runs
  ADD COLUMN IF NOT EXISTS metadata JSONB;

-- Create models table
CREATE TABLE cyl_models (
  id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  name TEXT NOT NULL,
  version TEXT NOT NULL,
  model_type TEXT NOT NULL CHECK (model_type IN ('primary', 'lateral', 'crown')),
  species TEXT NOT NULL,
  mode TEXT NOT NULL CHECK (mode IN ('cylinder', 'plate', 'turface')),
  age_range INT4RANGE,
  file_path TEXT NOT NULL,
  checksum TEXT NOT NULL,
  description TEXT,
  is_active BOOLEAN DEFAULT true,
  created_at TIMESTAMPTZ DEFAULT NOW(),
  updated_at TIMESTAMPTZ DEFAULT NOW(),
  UNIQUE(name, version)
);

-- Create pipeline run models junction table
CREATE TABLE cyl_pipeline_run_models (
  pipeline_run_id UUID NOT NULL REFERENCES cyl_pipeline_runs(id) ON DELETE CASCADE,
  model_id UUID NOT NULL REFERENCES cyl_models(id),
  model_type TEXT NOT NULL CHECK (model_type IN ('primary', 'lateral', 'crown')),
  PRIMARY KEY (pipeline_run_id, model_id)
);

-- Create index for querying runs by model
CREATE INDEX idx_pipeline_run_models_model ON cyl_pipeline_run_models(model_id);

-- Function to query runs by metadata
CREATE OR REPLACE FUNCTION search_pipeline_runs(
  p_species TEXT DEFAULT NULL,
  p_model_name TEXT DEFAULT NULL,
  p_status TEXT DEFAULT NULL,
  p_from_date TIMESTAMPTZ DEFAULT NULL,
  p_to_date TIMESTAMPTZ DEFAULT NULL
) RETURNS SETOF cyl_pipeline_runs AS $$
BEGIN
  RETURN QUERY
  SELECT r.*
  FROM cyl_pipeline_runs r
  WHERE
    (p_species IS NULL OR r.metadata->'parameters'->>'species' = p_species)
    AND (p_status IS NULL OR r.status = p_status)
    AND (p_from_date IS NULL OR r.created_at >= p_from_date)
    AND (p_to_date IS NULL OR r.created_at <= p_to_date)
    AND (
      p_model_name IS NULL OR
      r.metadata->'models'->'primary'->>'name' LIKE '%' || p_model_name || '%' OR
      r.metadata->'models'->'lateral'->>'name' LIKE '%' || p_model_name || '%'
    )
  ORDER BY r.created_at DESC;
END;
$$ LANGUAGE plpgsql;
```

---

## Tasks

### Phase 1: Schema Definition

- [ ] **3.1** Define metadata JSON schema v1.0.0
- [ ] **3.2** Create JSON Schema validation file
- [ ] **3.3** Document schema with examples
- [ ] **3.4** Create database migration for models table

### Phase 2: Metadata Collection

- [ ] **3.5** Update WorkflowTemplates with environment variable injection
- [ ] **3.6** Implement metadata collection in models-downloader container
- [ ] **3.7** Implement metadata collection in predictor container
- [ ] **3.8** Implement metadata collection in trait-extractor container
- [ ] **3.9** Create metadata aggregation step in workflow
- [ ] **3.10** Add checksum computation for outputs

### Phase 3: Storage & Retrieval

- [ ] **3.11** Implement metadata sync in results service
- [ ] **3.12** Create API endpoint: `GET /api/v1/pipeline/runs/{id}/metadata`
- [ ] **3.13** Create API endpoint: `GET /api/v1/pipeline/runs/search`
- [ ] **3.14** Generate human-readable summary markdown
- [ ] **3.15** Store summary alongside run outputs

### Phase 4: Model Registry

- [ ] **3.16** Seed initial models in cyl_models table
- [ ] **3.17** Implement model selection logic based on species/age
- [ ] **3.18** Link models to pipeline runs in cyl_pipeline_run_models
- [ ] **3.19** Add admin UI for model management (optional, can be DB direct)

### Phase 5: Testing

- [ ] **3.20** Test metadata capture for successful run
- [ ] **3.21** Test metadata capture for failed run (should still capture)
- [ ] **3.22** Test search by species/model/date
- [ ] **3.23** Verify checksum computation matches expected

---

## Acceptance Criteria

- [ ] Every pipeline run stores full metadata JSON
- [ ] Metadata includes Argo workflow UID for correlation
- [ ] Metadata includes container image digests for reproducibility
- [ ] Metadata includes model versions and checksums
- [ ] Metadata includes input/output checksums
- [ ] Failed runs still capture available metadata
- [ ] Runs can be searched by species, model, date range
- [ ] Human-readable summary generated for each run
- [ ] Schema version allows future evolution

---

## Future Enhancements

- Input data checksums (optional for large datasets)
- GPU metrics capture (memory, utilization)
- Network metrics (data transfer times)
- Cost estimation based on resource usage

---

## Labels

`metadata`, `provenance`, `reproducibility`, `P0`

## Assignees

- Schema design: TBD
- Container updates: TBD
- Backend API: TBD
