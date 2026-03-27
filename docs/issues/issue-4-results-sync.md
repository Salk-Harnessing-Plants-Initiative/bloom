# Issue 4: Results Sync - Traits & Predictions to Bloom

**EPIC**: Automated Root Trait Extraction Pipeline Integration
**Priority**: P0
**Dependencies**: Issue #2 (Pipeline Trigger), Issue #3 (Metadata & Provenance)
**Blocks**: Issue #5 (Downstream Analysis), Issue #6 (UI/UX)

---

## Summary

Implement automatic synchronization of pipeline results (traits and predictions) back to Bloom, with proper versioning, Box backup, and queryable trait storage.

## Background

Currently, pipeline outputs are manually processed:
1. Traits CSV copied to Box
2. Link shared via email
3. Scientists manually download and analyze

This issue automates the entire results flow:
1. Traits ingested into Supabase (queryable)
2. Predictions registered with version info (viewable)
3. Full outputs backed up to Box (archival)
4. Links stored in Bloom (accessible)

## Goals

1. Ingest traits from `traits_summary.csv` into Supabase
2. Register prediction files (.slp) with versioning
3. Automatic Box backup with link storage
4. Support re-runs with version preservation
5. Enable trait queries by experiment, scan, or run
6. Provide API for trait data access

## Technical Design

### Results Flow

```
Pipeline Completes
       │
       ▼
┌─────────────────────┐
│  Results Sync Job   │
│  (Triggered by      │
│   pipeline webhook) │
└──────────┬──────────┘
           │
           ├────────────────────────────────────────┐
           │                                        │
           ▼                                        ▼
┌─────────────────────┐                  ┌─────────────────────┐
│  Ingest Traits      │                  │  Register           │
│  - Parse CSV        │                  │  Predictions        │
│  - Map to scans     │                  │  - Link .slp files  │
│  - Insert to DB     │                  │  - Store paths      │
└──────────┬──────────┘                  └──────────┬──────────┘
           │                                        │
           └────────────────────────────────────────┘
                            │
                            ▼
                 ┌─────────────────────┐
                 │  Box Backup         │
                 │  (Async)            │
                 │  - Upload full      │
                 │    outputs          │
                 │  - Get share link   │
                 │  - Store in DB      │
                 └─────────────────────┘
```

### Trait Ingestion

#### CSV Format (from sleap-roots-traits)

```csv
Barcode,geno,rep,primary_root_length,lateral_root_count,total_root_length,...
BC_001,Genotype_A,1,125.3,12,456.7,...
BC_002,Genotype_A,2,130.1,15,489.2,...
```

The Barcode maps to `qr_code` in `cyl_plants`, which links to scans.

#### Ingestion Logic

```typescript
// src/services/results-sync.service.ts

interface TraitRow {
  barcode: string;
  [traitName: string]: string | number;
}

async function ingestTraits(
  pipelineRunId: string,
  traitsFilePath: string
): Promise<IngestResult> {
  // 1. Parse CSV
  const rows = await parseCSV<TraitRow>(traitsFilePath);

  // 2. Create or get trait source
  const traitSource = await getOrCreateTraitSource(pipelineRunId);

  // 3. Get trait definitions
  const traitDefs = await ensureTraitDefinitions(Object.keys(rows[0]));

  // 4. Map barcodes to scans
  const barcodeToScan = await mapBarcodesToScans(rows.map(r => r.barcode));

  // 5. Prepare trait values
  const scanTraits: ScanTraitInsert[] = [];
  const imageTraits: ImageTraitInsert[] = [];

  for (const row of rows) {
    const scan = barcodeToScan.get(row.barcode);
    if (!scan) {
      console.warn(`No scan found for barcode: ${row.barcode}`);
      continue;
    }

    // Extract trait values (skip metadata columns)
    for (const [traitName, value] of Object.entries(row)) {
      if (isMetadataColumn(traitName)) continue;
      if (value === null || value === '' || value === 'NaN') continue;

      const traitDef = traitDefs.get(traitName);
      if (!traitDef) continue;

      scanTraits.push({
        scan_id: scan.id,
        trait_id: traitDef.id,
        value: parseFloat(value as string),
        source_id: traitSource.id,
      });
    }
  }

  // 6. Insert traits (upsert to handle re-runs)
  const { error } = await supabase
    .from('cyl_scan_traits')
    .upsert(scanTraits, {
      onConflict: 'scan_id,trait_id,source_id',
      ignoreDuplicates: false,
    });

  if (error) throw error;

  return {
    rowsProcessed: rows.length,
    traitsInserted: scanTraits.length,
    traitSourceId: traitSource.id,
  };
}

async function getOrCreateTraitSource(pipelineRunId: string): Promise<TraitSource> {
  // Check if source exists for this run
  const { data: existing } = await supabase
    .from('cyl_trait_sources')
    .select('*')
    .eq('pipeline_run_id', pipelineRunId)
    .single();

  if (existing) return existing;

  // Create new source
  const { data: run } = await supabase
    .from('cyl_pipeline_runs')
    .select('*, cyl_experiments(*)')
    .eq('id', pipelineRunId)
    .single();

  const { data: newSource, error } = await supabase
    .from('cyl_trait_sources')
    .insert({
      name: `Pipeline Run ${run.id.slice(0, 8)}`,
      pipeline_run_id: pipelineRunId,
      version: await getNextVersion(run.experiment_id),
    })
    .select()
    .single();

  if (error) throw error;
  return newSource;
}

async function getNextVersion(experimentId: string): Promise<string> {
  const { data: sources } = await supabase
    .from('cyl_trait_sources')
    .select('version')
    .eq('pipeline_run_id', supabase.sql`
      SELECT id FROM cyl_pipeline_runs WHERE experiment_id = ${experimentId}
    `)
    .order('created_at', { ascending: false })
    .limit(1);

  if (!sources?.length) return 'v1';

  const lastVersion = sources[0].version;
  const versionNum = parseInt(lastVersion.replace('v', ''), 10);
  return `v${versionNum + 1}`;
}
```

### Prediction Registration

```typescript
interface PredictionFile {
  scanId: string;
  frameNumber: number;
  slpPath: string;
}

async function registerPredictions(
  pipelineRunId: string,
  predictionsDir: string
): Promise<void> {
  // 1. List all .slp files
  const slpFiles = await glob(`${predictionsDir}/**/*.slp`);

  // 2. Parse filenames to extract scan/frame info
  // Expected format: {qr_code}_frame_{n}.slp
  const predictions: PredictionInsert[] = [];

  for (const slpPath of slpFiles) {
    const filename = path.basename(slpPath, '.slp');
    const match = filename.match(/(.+)_frame_(\d+)/);
    if (!match) continue;

    const [, barcode, frameStr] = match;
    const frameNumber = parseInt(frameStr, 10);

    // Get image ID for this scan/frame
    const imageId = await getImageId(barcode, frameNumber);
    if (!imageId) {
      console.warn(`No image found for ${barcode} frame ${frameNumber}`);
      continue;
    }

    predictions.push({
      image_id: imageId,
      pipeline_run_id: pipelineRunId,
      object_path: slpPath.replace('/hpi/hpi_dev/bloom/', ''), // Relative path
    });
  }

  // 3. Insert predictions
  const { error } = await supabase
    .from('cyl_predictions')
    .upsert(predictions, {
      onConflict: 'image_id,pipeline_run_id',
    });

  if (error) throw error;

  console.log(`Registered ${predictions.length} predictions`);
}

async function getImageId(barcode: string, frameNumber: number): Promise<string | null> {
  const { data } = await supabase
    .from('cyl_images')
    .select('id, cyl_scans!inner(cyl_plants!inner(qr_code))')
    .eq('cyl_scans.cyl_plants.qr_code', barcode)
    .eq('frame_number', frameNumber)
    .single();

  return data?.id || null;
}
```

### Box Backup

```typescript
async function backupToBox(
  pipelineRunId: string,
  outputPath: string
): Promise<string> {
  // 1. Get experiment info for folder naming
  const { data: run } = await supabase
    .from('cyl_pipeline_runs')
    .select('*, cyl_experiments(name)')
    .eq('id', pipelineRunId)
    .single();

  const experimentName = run.cyl_experiments.name.replace(/\s+/g, '_');
  const date = new Date().toISOString().slice(0, 10).replace(/-/g, '');
  const folderName = `${date}_${experimentName}_run_${pipelineRunId.slice(0, 8)}`;

  // 2. Upload to Box via rclone
  const boxRemote = 'box:Phenotyping_team_GH/sleap-roots-pipeline-results';
  const boxPath = `${boxRemote}/${folderName}`;

  await execAsync(`rclone copy --update -P "${outputPath}" "${boxPath}"`);

  // 3. Get shareable link
  const linkResult = await execAsync(`rclone link "${boxPath}"`);
  const boxLink = linkResult.stdout.trim();

  // 4. Store link in database
  await supabase
    .from('cyl_pipeline_runs')
    .update({
      box_link: boxLink,
      box_path: boxPath,
    })
    .eq('id', pipelineRunId);

  return boxLink;
}
```

### Full Results Sync Service

```typescript
// src/services/results-sync.service.ts

export class ResultsSyncService {
  /**
   * Called when pipeline completes successfully
   */
  async syncResults(pipelineRunId: string): Promise<SyncResult> {
    const run = await this.getPipelineRun(pipelineRunId);
    const outputPath = `/hpi/hpi_dev/bloom/experiments/${run.experiment_id}/pipeline_outputs/${pipelineRunId}`;

    const result: SyncResult = {
      pipelineRunId,
      traitsIngested: 0,
      predictionsRegistered: 0,
      boxLink: null,
      errors: [],
    };

    // 1. Ingest traits
    try {
      const traitsFile = path.join(outputPath, 'traits', 'traits_summary.csv');
      const ingestResult = await this.ingestTraits(pipelineRunId, traitsFile);
      result.traitsIngested = ingestResult.traitsInserted;
    } catch (error) {
      result.errors.push(`Trait ingestion failed: ${error.message}`);
    }

    // 2. Register predictions
    try {
      const predictionsDir = path.join(outputPath, 'predictions');
      const predCount = await this.registerPredictions(pipelineRunId, predictionsDir);
      result.predictionsRegistered = predCount;
    } catch (error) {
      result.errors.push(`Prediction registration failed: ${error.message}`);
    }

    // 3. Sync metadata (from Issue #3)
    try {
      await this.syncMetadata(pipelineRunId, outputPath);
    } catch (error) {
      result.errors.push(`Metadata sync failed: ${error.message}`);
    }

    // 4. Box backup (async, don't block)
    this.backupToBox(pipelineRunId, outputPath)
      .then(link => {
        result.boxLink = link;
        console.log(`Box backup complete: ${link}`);
      })
      .catch(error => {
        console.error(`Box backup failed: ${error.message}`);
      });

    // 5. Update pipeline run status
    await supabase
      .from('cyl_pipeline_runs')
      .update({
        sync_status: result.errors.length > 0 ? 'partial' : 'complete',
        sync_result: result,
      })
      .eq('id', pipelineRunId);

    return result;
  }
}
```

### Trait Query API

```typescript
// GET /api/v1/experiments/{id}/traits
async function getExperimentTraits(
  experimentId: string,
  options: {
    traitNames?: string[];
    sourceVersion?: string;
    format?: 'json' | 'csv';
  }
): Promise<TraitData> {
  let query = supabase
    .from('cyl_scan_traits')
    .select(`
      value,
      cyl_traits(name),
      cyl_trait_sources(version, pipeline_run_id),
      cyl_scans!inner(
        id,
        plant_age_days,
        cyl_plants!inner(
          qr_code,
          cyl_waves!inner(
            number,
            cyl_experiments!inner(id)
          )
        )
      )
    `)
    .eq('cyl_scans.cyl_plants.cyl_waves.cyl_experiments.id', experimentId);

  if (options.traitNames?.length) {
    query = query.in('cyl_traits.name', options.traitNames);
  }

  if (options.sourceVersion) {
    query = query.eq('cyl_trait_sources.version', options.sourceVersion);
  }

  const { data, error } = await query;
  if (error) throw error;

  if (options.format === 'csv') {
    return convertToCSV(data);
  }

  return data;
}

// GET /api/v1/scans/{id}/traits
async function getScanTraits(scanId: string): Promise<TraitData> {
  const { data, error } = await supabase
    .from('cyl_scan_traits')
    .select(`
      value,
      cyl_traits(name, description),
      cyl_trait_sources(version, created_at)
    `)
    .eq('scan_id', scanId)
    .order('cyl_traits.name');

  if (error) throw error;
  return data;
}

// GET /api/v1/pipeline/runs/{id}/traits
async function getPipelineRunTraits(runId: string): Promise<TraitData> {
  const { data: source } = await supabase
    .from('cyl_trait_sources')
    .select('id')
    .eq('pipeline_run_id', runId)
    .single();

  if (!source) throw new Error('No traits found for this pipeline run');

  const { data, error } = await supabase
    .from('cyl_scan_traits')
    .select(`
      value,
      cyl_traits(name),
      cyl_scans(id, cyl_plants(qr_code))
    `)
    .eq('source_id', source.id);

  if (error) throw error;
  return data;
}
```

---

## Database Schema Changes

```sql
-- Extend cyl_trait_sources to link to pipeline runs
ALTER TABLE cyl_trait_sources
  ADD COLUMN pipeline_run_id UUID REFERENCES cyl_pipeline_runs(id),
  ADD COLUMN version TEXT,
  ADD COLUMN created_at TIMESTAMPTZ DEFAULT NOW();

-- Create index for querying by pipeline run
CREATE INDEX idx_trait_sources_pipeline_run ON cyl_trait_sources(pipeline_run_id);

-- Create predictions table
CREATE TABLE cyl_predictions (
  id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  image_id UUID NOT NULL REFERENCES cyl_images(id) ON DELETE CASCADE,
  pipeline_run_id UUID NOT NULL REFERENCES cyl_pipeline_runs(id) ON DELETE CASCADE,
  object_path TEXT NOT NULL,
  model_id UUID REFERENCES cyl_models(id),
  created_at TIMESTAMPTZ DEFAULT NOW(),
  UNIQUE(image_id, pipeline_run_id)
);

-- Indexes for predictions
CREATE INDEX idx_predictions_image ON cyl_predictions(image_id);
CREATE INDEX idx_predictions_pipeline_run ON cyl_predictions(pipeline_run_id);

-- Add Box backup fields to pipeline_runs
ALTER TABLE cyl_pipeline_runs
  ADD COLUMN box_link TEXT,
  ADD COLUMN box_path TEXT,
  ADD COLUMN sync_status TEXT CHECK (sync_status IN ('pending', 'syncing', 'complete', 'partial', 'failed')),
  ADD COLUMN sync_result JSONB;

-- Ensure unique constraint for trait values per source
ALTER TABLE cyl_scan_traits
  ADD CONSTRAINT unique_scan_trait_source UNIQUE (scan_id, trait_id, source_id);

-- View for easy trait queries with scan context
CREATE OR REPLACE VIEW cyl_scan_traits_extended AS
SELECT
  st.id,
  st.value,
  t.name as trait_name,
  t.description as trait_description,
  ts.version as source_version,
  ts.pipeline_run_id,
  s.id as scan_id,
  s.plant_age_days,
  p.qr_code,
  p.id as plant_id,
  w.number as wave_number,
  e.id as experiment_id,
  e.name as experiment_name
FROM cyl_scan_traits st
JOIN cyl_traits t ON st.trait_id = t.id
JOIN cyl_trait_sources ts ON st.source_id = ts.id
JOIN cyl_scans s ON st.scan_id = s.id
JOIN cyl_plants p ON s.plant_id = p.id
JOIN cyl_waves w ON p.wave_id = w.id
JOIN cyl_experiments e ON w.experiment_id = e.id;

-- Function to get latest traits for an experiment
CREATE OR REPLACE FUNCTION get_latest_experiment_traits(p_experiment_id UUID)
RETURNS TABLE (
  scan_id UUID,
  qr_code TEXT,
  plant_age_days INTEGER,
  trait_name TEXT,
  value REAL,
  source_version TEXT
) AS $$
BEGIN
  RETURN QUERY
  WITH latest_source AS (
    SELECT ts.id
    FROM cyl_trait_sources ts
    JOIN cyl_pipeline_runs pr ON ts.pipeline_run_id = pr.id
    WHERE pr.experiment_id = p_experiment_id
      AND pr.status = 'completed'
    ORDER BY ts.created_at DESC
    LIMIT 1
  )
  SELECT
    ste.scan_id,
    ste.qr_code,
    ste.plant_age_days,
    ste.trait_name,
    ste.value,
    ste.source_version
  FROM cyl_scan_traits_extended ste
  WHERE ste.source_id = (SELECT id FROM latest_source)
  ORDER BY ste.qr_code, ste.trait_name;
END;
$$ LANGUAGE plpgsql;
```

---

## Tasks

### Phase 1: Trait Ingestion

- [ ] **4.1** Create trait ingestion service
- [ ] **4.2** Implement CSV parsing with validation
- [ ] **4.3** Implement barcode-to-scan mapping
- [ ] **4.4** Implement trait source versioning
- [ ] **4.5** Handle re-runs (upsert logic)
- [ ] **4.6** Add error handling for missing scans

### Phase 2: Prediction Registration

- [ ] **4.7** Create prediction registration service
- [ ] **4.8** Implement .slp file discovery
- [ ] **4.9** Implement image ID lookup
- [ ] **4.10** Store prediction paths with run version

### Phase 3: Box Backup

- [ ] **4.11** Implement rclone integration
- [ ] **4.12** Generate folder naming convention
- [ ] **4.13** Get and store shareable link
- [ ] **4.14** Handle backup failures gracefully
- [ ] **4.15** Add retry logic for transient failures

### Phase 4: API Endpoints

- [ ] **4.16** `GET /api/v1/experiments/{id}/traits`
- [ ] **4.17** `GET /api/v1/scans/{id}/traits`
- [ ] **4.18** `GET /api/v1/pipeline/runs/{id}/traits`
- [ ] **4.19** `GET /api/v1/pipeline/runs/{id}/predictions`
- [ ] **4.20** Add CSV export option
- [ ] **4.21** Add trait filtering by name

### Phase 5: Integration

- [ ] **4.22** Wire up results sync to pipeline completion webhook
- [ ] **4.23** Add sync status to pipeline run status endpoint
- [ ] **4.24** Update pipeline service to trigger sync
- [ ] **4.25** Add Box link to experiment/run UI (placeholder for Issue #6)

### Phase 6: Testing

- [ ] **4.26** Test trait ingestion with sample CSV
- [ ] **4.27** Test prediction registration
- [ ] **4.28** Test Box backup and link retrieval
- [ ] **4.29** Test re-run creates new version
- [ ] **4.30** Test trait queries return correct data

---

## Acceptance Criteria

- [ ] Traits from pipeline output ingested into `cyl_scan_traits`
- [ ] Each pipeline run creates a new trait source version
- [ ] Predictions linked to images with pipeline run reference
- [ ] Box backup runs automatically on pipeline completion
- [ ] Box link stored and accessible in pipeline run record
- [ ] Trait data queryable via API (JSON and CSV formats)
- [ ] Re-runs preserve old traits (versioned, not overwritten)
- [ ] Errors during sync don't crash the service (graceful handling)

---

## API Reference

### Get Experiment Traits

```
GET /api/v1/experiments/{id}/traits
```

**Query Parameters:**
- `traits`: Comma-separated trait names to filter
- `version`: Specific source version (default: latest)
- `format`: `json` or `csv` (default: json)

**Response:**
```json
{
  "experiment_id": "uuid",
  "source_version": "v3",
  "pipeline_run_id": "uuid",
  "traits": [
    {
      "scan_id": "uuid",
      "qr_code": "BC_001",
      "plant_age_days": 5,
      "values": {
        "primary_root_length": 125.3,
        "lateral_root_count": 12,
        "total_root_length": 456.7
      }
    }
  ]
}
```

### Get Pipeline Run Predictions

```
GET /api/v1/pipeline/runs/{id}/predictions
```

**Response:**
```json
{
  "pipeline_run_id": "uuid",
  "prediction_count": 1800,
  "predictions": [
    {
      "image_id": "uuid",
      "frame_number": 0,
      "object_path": "experiments/.../predictions/BC_001_frame_0.slp",
      "scan_id": "uuid",
      "qr_code": "BC_001"
    }
  ]
}
```

---

## Labels

`backend`, `data-sync`, `api`, `P0`

## Assignees

- Trait ingestion: TBD
- Prediction registration: TBD
- Box integration: TBD
- API: TBD
