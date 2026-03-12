# Issue 5: Downstream Analysis - sleap-roots-analyze Integration

**EPIC**: Automated Root Trait Extraction Pipeline Integration
**Priority**: P1
**Dependencies**: Issue #4 (Results Sync)
**Blocks**: None (enables future GWAS integration)

---

## Summary

Integrate the `sleap-roots-analyze` QC and statistical analysis pipeline as an optional step in the automated workflow, providing quality control, heritability analysis, and publication-ready visualizations.

## Background

After trait extraction, scientists typically need to:
1. Clean data (remove outliers, filter low-quality samples)
2. Calculate heritability (H²) to identify robust traits
3. Generate visualizations (PCA, correlation heatmaps)
4. Prepare data for downstream analysis (GWAS)

The [sleap-roots-analyze](https://github.com/talmolab/sleap-roots-analyze) package provides a comprehensive pipeline for this, including:
- Automated outlier detection (Mahalanobis, Isolation Forest)
- Heritability calculation and filtering
- Publication-quality static and interactive plots
- Cross-platform trait comparison

## Goals

1. Add sleap-roots-analyze as an optional workflow step
2. Store QC results and heritability in Bloom database
3. Make analysis outputs accessible via Bloom UI
4. Enable automated GWAS triggering (future)
5. Provide configurable analysis parameters

## Technical Design

### Extended Pipeline Architecture

```
┌─────────────────────────────────────────────────────────────────────────┐
│                        Argo Workflow (Extended)                          │
│                                                                          │
│  ┌─────────────┐   ┌─────────────┐   ┌─────────────────┐                │
│  │   models-   │──▶│  predictor  │──▶│ trait-extractor │                │
│  │  downloader │   │   (GPU)     │   │                 │                │
│  └─────────────┘   └─────────────┘   └────────┬────────┘                │
│                                               │                          │
│                                               ▼                          │
│                                      ┌─────────────────┐                │
│                                      │  results-sync   │                │
│                                      │  (to Supabase)  │                │
│                                      └────────┬────────┘                │
│                                               │                          │
│                         ┌─────────────────────┼─────────────────────┐   │
│                         │ (if analysis enabled)                     │   │
│                         ▼                                           │   │
│                ┌─────────────────┐   ┌─────────────────┐            │   │
│                │   QC Pipeline   │──▶│  Viz Pipeline   │            │   │
│                │ (sleap-roots-   │   │ (sleap-roots-   │            │   │
│                │  analyze qc)    │   │  analyze viz)   │            │   │
│                └────────┬────────┘   └────────┬────────┘            │   │
│                         │                     │                      │   │
│                         ▼                     ▼                      │   │
│                ┌─────────────────────────────────────────┐          │   │
│                │         analysis-results-sync           │          │   │
│                │  - Store heritability                   │          │   │
│                │  - Store QC flags                       │          │   │
│                │  - Link visualizations                  │          │   │
│                └─────────────────────────────────────────┘          │   │
│                                                                      │   │
└──────────────────────────────────────────────────────────────────────────┘
```

### Workflow Extension

```yaml
# sleap-roots-pipeline-with-analysis.yaml

apiVersion: argoproj.io/v1alpha1
kind: Workflow
metadata:
  generateName: sleap-roots-pipeline-full-
spec:
  entrypoint: pipeline-with-analysis
  arguments:
    parameters:
    # ... existing parameters ...
    - name: run-analysis
      value: "true"  # Enable/disable analysis
    - name: analysis-config
      value: ""  # Optional custom config path
    - name: heritability-threshold
      value: "0.4"
    - name: outlier-method
      value: "mahalanobis"

  templates:
  - name: pipeline-with-analysis
    dag:
      tasks:
      # ... existing tasks (models-downloader, predictor, trait-extractor, results-sync) ...

      # Conditional analysis tasks
      - name: qc-pipeline
        dependencies: [results-sync]
        when: "{{workflow.parameters.run-analysis}} == true"
        templateRef:
          name: sleap-roots-analyze-qc-template
          template: qc-pipeline
        arguments:
          parameters:
          - name: input-csv
            value: "{{workflow.parameters.output-path}}/traits/traits_summary.csv"
          - name: output-dir
            value: "{{workflow.parameters.output-path}}/analysis/qc"
          - name: heritability-threshold
            value: "{{workflow.parameters.heritability-threshold}}"
          - name: outlier-method
            value: "{{workflow.parameters.outlier-method}}"

      - name: viz-pipeline
        dependencies: [qc-pipeline]
        when: "{{workflow.parameters.run-analysis}} == true"
        templateRef:
          name: sleap-roots-analyze-viz-template
          template: viz-pipeline
        arguments:
          parameters:
          - name: input-csv
            value: "{{workflow.parameters.output-path}}/analysis/qc/10_final_data.csv"
          - name: output-dir
            value: "{{workflow.parameters.output-path}}/analysis/viz"
          - name: image-dir
            value: "{{workflow.parameters.input-path}}"

      - name: analysis-results-sync
        dependencies: [viz-pipeline]
        when: "{{workflow.parameters.run-analysis}} == true"
        templateRef:
          name: bloom-analysis-sync-template
          template: sync-analysis
        arguments:
          parameters:
          - name: pipeline-run-id
            value: "{{workflow.parameters.pipeline-run-id}}"
          - name: analysis-dir
            value: "{{workflow.parameters.output-path}}/analysis"
```

### QC Pipeline WorkflowTemplate

```yaml
# sleap-roots-analyze-qc-template.yaml

apiVersion: argoproj.io/v1alpha1
kind: WorkflowTemplate
metadata:
  name: sleap-roots-analyze-qc-template
  namespace: runai-tye-lab
spec:
  templates:
  - name: qc-pipeline
    inputs:
      parameters:
      - name: input-csv
      - name: output-dir
      - name: heritability-threshold
        default: "0.4"
      - name: outlier-method
        default: "mahalanobis"
      - name: max-nan-fraction
        default: "0.2"
    container:
      image: ghcr.io/talmolab/sleap-roots-analyze:latest
      command: ["sleap-roots-analyze", "qc"]
      args:
      - "--input"
      - "{{inputs.parameters.input-csv}}"
      - "--output"
      - "{{inputs.parameters.output-dir}}"
      - "--heritability-threshold"
      - "{{inputs.parameters.heritability-threshold}}"
      - "--outlier-method"
      - "{{inputs.parameters.outlier-method}}"
      - "--max-nan-fraction"
      - "{{inputs.parameters.max-nan-fraction}}"
      volumeMounts:
      - name: bloom-data
        mountPath: /hpi/hpi_dev/bloom
      resources:
        requests:
          memory: "16Gi"
          cpu: "4"
        limits:
          memory: "32Gi"
          cpu: "8"
    retryStrategy:
      limit: 2
      retryPolicy: OnFailure
```

### Analysis Results Sync

```typescript
// src/services/analysis-sync.service.ts

interface AnalysisSyncResult {
  pipelineRunId: string;
  analysisRunId: string;
  qcMetrics: QCMetrics;
  heritabilityCount: number;
  visualizationsCount: number;
}

interface QCMetrics {
  inputSamples: number;
  outputSamples: number;
  removedOutliers: number;
  removedTraits: number;
  passedHeritability: number;
}

export class AnalysisSyncService {
  async syncAnalysisResults(
    pipelineRunId: string,
    analysisDir: string
  ): Promise<AnalysisSyncResult> {
    // 1. Create analysis run record
    const analysisRun = await this.createAnalysisRun(pipelineRunId, 'qc');

    // 2. Parse QC summary
    const qcSummaryPath = path.join(analysisDir, 'qc', 'pipeline_summary.json');
    const qcSummary = JSON.parse(await fs.readFile(qcSummaryPath, 'utf-8'));

    // 3. Ingest heritability results
    const heritabilityPath = path.join(analysisDir, 'qc', '08_heritability_results.csv');
    const heritabilityCount = await this.ingestHeritability(analysisRun.id, heritabilityPath);

    // 4. Store QC flags for samples
    const removedSamplesPath = path.join(analysisDir, 'qc', '02_removed_samples_detail.csv');
    await this.storeQCFlags(analysisRun.id, removedSamplesPath);

    // 5. Register visualizations
    const vizDir = path.join(analysisDir, 'viz', 'static_figures');
    const vizCount = await this.registerVisualizations(analysisRun.id, vizDir);

    // 6. Update analysis run with summary
    const qcMetrics: QCMetrics = {
      inputSamples: qcSummary.steps[0]?.metadata?.rows || 0,
      outputSamples: qcSummary.steps[9]?.metadata?.rows || 0,
      removedOutliers: qcSummary.steps[6]?.metadata?.removed_count || 0,
      removedTraits: qcSummary.steps[1]?.metadata?.removed_traits || 0,
      passedHeritability: heritabilityCount,
    };

    await supabase
      .from('cyl_analysis_runs')
      .update({
        status: 'completed',
        results_summary: qcMetrics,
        completed_at: new Date().toISOString(),
      })
      .eq('id', analysisRun.id);

    return {
      pipelineRunId,
      analysisRunId: analysisRun.id,
      qcMetrics,
      heritabilityCount,
      visualizationsCount: vizCount,
    };
  }

  private async ingestHeritability(
    analysisRunId: string,
    heritabilityPath: string
  ): Promise<number> {
    const rows = await parseCSV(heritabilityPath);

    const heritabilityRecords = [];
    for (const row of rows) {
      // Get or create trait definition
      const trait = await this.getOrCreateTrait(row.trait_name);

      heritabilityRecords.push({
        analysis_run_id: analysisRunId,
        trait_id: trait.id,
        heritability: parseFloat(row.heritability),
        p_value: parseFloat(row.p_value),
        n_samples: parseInt(row.n_samples, 10),
        passed_threshold: row.passed === 'true',
      });
    }

    await supabase
      .from('cyl_trait_heritability')
      .upsert(heritabilityRecords, {
        onConflict: 'analysis_run_id,trait_id',
      });

    return heritabilityRecords.filter(r => r.passed_threshold).length;
  }

  private async storeQCFlags(
    analysisRunId: string,
    removedSamplesPath: string
  ): Promise<void> {
    if (!await fileExists(removedSamplesPath)) return;

    const rows = await parseCSV(removedSamplesPath);

    const qcFlags = rows.map(row => ({
      analysis_run_id: analysisRunId,
      barcode: row.barcode,
      flag_type: 'outlier',
      reason: row.reason,
      method: row.detection_method,
    }));

    await supabase
      .from('cyl_analysis_qc_flags')
      .insert(qcFlags);
  }

  private async registerVisualizations(
    analysisRunId: string,
    vizDir: string
  ): Promise<number> {
    const vizFiles = await glob(`${vizDir}/**/*.{png,pdf,html}`);

    const visualizations = vizFiles.map(filePath => ({
      analysis_run_id: analysisRunId,
      file_type: path.extname(filePath).slice(1),
      file_name: path.basename(filePath),
      object_path: filePath.replace('/hpi/hpi_dev/bloom/', ''),
      category: this.categorizeVisualization(path.basename(filePath)),
    }));

    await supabase
      .from('cyl_analysis_visualizations')
      .insert(visualizations);

    return visualizations.length;
  }

  private categorizeVisualization(filename: string): string {
    if (filename.includes('pca')) return 'pca';
    if (filename.includes('heritability')) return 'heritability';
    if (filename.includes('correlation')) return 'correlation';
    if (filename.includes('distribution')) return 'distribution';
    if (filename.includes('outlier')) return 'outlier';
    return 'other';
  }
}
```

### Analysis API Endpoints

```typescript
// GET /api/v1/pipeline/runs/{id}/analysis
async function getAnalysisResults(runId: string) {
  const { data: analysisRun } = await supabase
    .from('cyl_analysis_runs')
    .select(`
      *,
      cyl_trait_heritability(
        heritability,
        p_value,
        passed_threshold,
        cyl_traits(name)
      ),
      cyl_analysis_visualizations(
        file_name,
        object_path,
        category
      )
    `)
    .eq('pipeline_run_id', runId)
    .single();

  return analysisRun;
}

// GET /api/v1/experiments/{id}/heritability
async function getExperimentHeritability(experimentId: string) {
  // Get latest analysis run for experiment
  const { data } = await supabase
    .from('cyl_trait_heritability')
    .select(`
      heritability,
      p_value,
      n_samples,
      passed_threshold,
      cyl_traits(name, description),
      cyl_analysis_runs!inner(
        cyl_pipeline_runs!inner(experiment_id)
      )
    `)
    .eq('cyl_analysis_runs.cyl_pipeline_runs.experiment_id', experimentId)
    .order('heritability', { ascending: false });

  return data;
}

// GET /api/v1/pipeline/runs/{id}/visualizations
async function getVisualizations(runId: string) {
  const { data } = await supabase
    .from('cyl_analysis_visualizations')
    .select('*')
    .eq('analysis_run_id', supabase.sql`
      SELECT id FROM cyl_analysis_runs WHERE pipeline_run_id = ${runId}
    `)
    .order('category');

  return data;
}
```

---

## Database Schema Changes

```sql
-- Analysis runs table
CREATE TABLE cyl_analysis_runs (
  id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  pipeline_run_id UUID NOT NULL REFERENCES cyl_pipeline_runs(id) ON DELETE CASCADE,
  analysis_type TEXT NOT NULL CHECK (analysis_type IN ('qc', 'viz', 'cross_platform', 'gwas')),
  status TEXT NOT NULL DEFAULT 'pending'
    CHECK (status IN ('pending', 'running', 'completed', 'failed')),
  config JSONB,
  results_summary JSONB,
  output_path TEXT,
  started_at TIMESTAMPTZ,
  completed_at TIMESTAMPTZ,
  error_message TEXT,
  created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_analysis_runs_pipeline ON cyl_analysis_runs(pipeline_run_id);

-- Heritability results per trait
CREATE TABLE cyl_trait_heritability (
  id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  analysis_run_id UUID NOT NULL REFERENCES cyl_analysis_runs(id) ON DELETE CASCADE,
  trait_id UUID NOT NULL REFERENCES cyl_traits(id),
  heritability REAL NOT NULL,
  p_value REAL,
  n_samples INTEGER,
  passed_threshold BOOLEAN DEFAULT false,
  created_at TIMESTAMPTZ DEFAULT NOW(),
  UNIQUE(analysis_run_id, trait_id)
);

CREATE INDEX idx_heritability_analysis ON cyl_trait_heritability(analysis_run_id);
CREATE INDEX idx_heritability_trait ON cyl_trait_heritability(trait_id);
CREATE INDEX idx_heritability_passed ON cyl_trait_heritability(passed_threshold) WHERE passed_threshold = true;

-- QC flags for samples
CREATE TABLE cyl_analysis_qc_flags (
  id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  analysis_run_id UUID NOT NULL REFERENCES cyl_analysis_runs(id) ON DELETE CASCADE,
  barcode TEXT NOT NULL,
  flag_type TEXT NOT NULL CHECK (flag_type IN ('outlier', 'missing_data', 'invalid', 'manual')),
  reason TEXT,
  method TEXT,
  created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_qc_flags_analysis ON cyl_analysis_qc_flags(analysis_run_id);
CREATE INDEX idx_qc_flags_barcode ON cyl_analysis_qc_flags(barcode);

-- Visualization files
CREATE TABLE cyl_analysis_visualizations (
  id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  analysis_run_id UUID NOT NULL REFERENCES cyl_analysis_runs(id) ON DELETE CASCADE,
  file_type TEXT NOT NULL CHECK (file_type IN ('png', 'pdf', 'html', 'json')),
  file_name TEXT NOT NULL,
  object_path TEXT NOT NULL,
  category TEXT,
  description TEXT,
  created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_viz_analysis ON cyl_analysis_visualizations(analysis_run_id);
CREATE INDEX idx_viz_category ON cyl_analysis_visualizations(category);

-- View for trait summary with heritability
CREATE OR REPLACE VIEW cyl_traits_with_heritability AS
SELECT
  t.id,
  t.name,
  t.description,
  h.heritability,
  h.p_value,
  h.passed_threshold,
  ar.pipeline_run_id,
  pr.experiment_id
FROM cyl_traits t
LEFT JOIN cyl_trait_heritability h ON t.id = h.trait_id
LEFT JOIN cyl_analysis_runs ar ON h.analysis_run_id = ar.id
LEFT JOIN cyl_pipeline_runs pr ON ar.pipeline_run_id = pr.id;
```

---

## Tasks

### Phase 1: Container & Template

- [ ] **5.1** Build sleap-roots-analyze Docker container
- [ ] **5.2** Push to container registry (ghcr.io/talmolab)
- [ ] **5.3** Create QC pipeline WorkflowTemplate
- [ ] **5.4** Create Viz pipeline WorkflowTemplate
- [ ] **5.5** Test templates independently

### Phase 2: Workflow Integration

- [ ] **5.6** Extend main workflow with analysis steps
- [ ] **5.7** Add conditional execution (`when` clause)
- [ ] **5.8** Add analysis parameters to workflow
- [ ] **5.9** Create analysis-results-sync template
- [ ] **5.10** Test full workflow with analysis enabled

### Phase 3: Results Sync

- [ ] **5.11** Implement AnalysisSyncService
- [ ] **5.12** Ingest heritability results
- [ ] **5.13** Store QC flags
- [ ] **5.14** Register visualizations
- [ ] **5.15** Handle sync failures gracefully

### Phase 4: API

- [ ] **5.16** `GET /api/v1/pipeline/runs/{id}/analysis`
- [ ] **5.17** `GET /api/v1/experiments/{id}/heritability`
- [ ] **5.18** `GET /api/v1/pipeline/runs/{id}/visualizations`
- [ ] **5.19** Add analysis options to pipeline trigger API

### Phase 5: UI Integration (Placeholder)

- [ ] **5.20** Add "Run Analysis" option to pipeline trigger UI
- [ ] **5.21** Display heritability in traits view (Issue #6)
- [ ] **5.22** Display visualizations in results view (Issue #6)

### Phase 6: Testing

- [ ] **5.23** Test QC pipeline with sample data
- [ ] **5.24** Test viz pipeline output
- [ ] **5.25** Test heritability ingestion
- [ ] **5.26** Test visualization registration
- [ ] **5.27** E2E test: full pipeline with analysis

---

## Acceptance Criteria

- [ ] Analysis runs as optional workflow step
- [ ] QC metrics stored in database
- [ ] Heritability calculated and queryable
- [ ] Visualizations accessible via API
- [ ] QC flags identify outlier samples
- [ ] Analysis can be skipped if not needed
- [ ] Failed analysis doesn't block main pipeline results

---

## Future Enhancements

- **GWAS Integration**: Auto-trigger GWAS pipeline when traits + genotypes available
- **Cross-Platform Analysis**: Compare traits across different phenotyping platforms
- **Custom Configs**: Allow scientists to upload custom analysis configurations
- **Interactive Dashboards**: Embed Plotly visualizations in Bloom UI

---

## Labels

`analysis`, `downstream`, `P1`

## Assignees

- Container build: TBD
- Workflow integration: TBD
- Results sync: TBD
