# Issue 2: Pipeline Trigger - Manual Execution via Bloom

**EPIC**: Automated Root Trait Extraction Pipeline Integration
**Priority**: P0
**Dependencies**: Issue #1 (Infrastructure)
**Blocks**: Issue #4 (Results Sync)

---

## Summary

Implement the ability to trigger sleap-roots pipeline execution from Bloom, with queue management, resource limits, and status tracking.

## Background

Currently, pipeline execution requires:
1. Manual SSH to workstation
2. Running CLI commands to download data
3. Executing Docker containers locally
4. Manually uploading results

This issue implements a "Run Pipeline" capability that submits jobs to the RunAI GPU cluster via Argo Workflows, with proper queue management to be a good cluster citizen.

## Goals

1. API endpoint to trigger pipeline for an experiment
2. CLI command for power users
3. Basic web UI button (detailed UI in Issue #6)
4. Queue management with resource limits
5. Real-time status tracking
6. Support for partial runs (specific waves/ages)

## Technical Design

### API Design

#### Trigger Pipeline

```
POST /api/v1/pipeline/trigger
```

**Request Body:**
```json
{
  "experiment_id": "uuid",
  "waves": [1, 2, 3],           // Optional: specific waves (default: all)
  "min_age": 2,                  // Optional: filter by age
  "max_age": 14,
  "model_override": {            // Optional: admin only
    "primary_model_id": "model-uuid",
    "lateral_model_id": "model-uuid"
  },
  "priority": "normal"           // normal | high (admin only)
}
```

**Response (202 Accepted):**
```json
{
  "pipeline_run_id": "uuid",
  "status": "queued",
  "position_in_queue": 3,
  "estimated_start": "2026-02-11T15:30:00Z",
  "links": {
    "status": "/api/v1/pipeline/runs/{id}",
    "cancel": "/api/v1/pipeline/runs/{id}/cancel",
    "logs": "/api/v1/pipeline/runs/{id}/logs"
  }
}
```

#### Get Pipeline Status

```
GET /api/v1/pipeline/runs/{id}
```

**Response:**
```json
{
  "id": "uuid",
  "experiment_id": "uuid",
  "status": "running",
  "phase": "predictor",
  "progress": {
    "models_downloader": "completed",
    "predictor": "running",
    "trait_extractor": "pending",
    "results_sync": "pending"
  },
  "argo_workflow_name": "sleap-roots-pipeline-abc123",
  "started_at": "2026-02-11T15:32:00Z",
  "estimated_completion": "2026-02-11T16:15:00Z",
  "scan_count": 450,
  "scans_processed": 230
}
```

#### List Pipeline Runs

```
GET /api/v1/pipeline/runs?experiment_id={id}&status={status}
```

#### Cancel Pipeline

```
POST /api/v1/pipeline/runs/{id}/cancel
```

#### Get Pipeline Logs

```
GET /api/v1/pipeline/runs/{id}/logs?follow=true
```

### Queue Management

#### Resource Limits

| Limit | Value | Rationale |
|-------|-------|-----------|
| Max concurrent GPU jobs (bloom automation) | 4 | ~2 full GPUs, leaves room for manual jobs |
| Max queued jobs per user | 5 | Prevent single user monopolizing queue |
| Max total queue depth | 20 | Prevent unbounded growth |
| Job timeout | 4 hours | Prevent stuck jobs blocking queue |

#### Queue Implementation

```typescript
// Queue states: queued → submitting → running → completed/failed

interface QueueManager {
  // Check if we can submit a new job
  canSubmit(): Promise<boolean>;

  // Get current running count
  getRunningCount(): Promise<number>;

  // Get queue position for a job
  getQueuePosition(runId: string): Promise<number>;

  // Process queue (called periodically and on job completion)
  processQueue(): Promise<void>;
}

class PipelineQueueManager implements QueueManager {
  private readonly MAX_CONCURRENT = 4;

  async canSubmit(): Promise<boolean> {
    const running = await this.getRunningCount();
    return running < this.MAX_CONCURRENT;
  }

  async getRunningCount(): Promise<number> {
    // Count Argo workflows in Running state
    const result = await execAsync('argo', [
      'list', '-n', ARGO_NAMESPACE,
      '--running', '-l', 'managed-by=bloom',
      '--output', 'json'
    ]);
    const workflows = JSON.parse(result.stdout);
    return workflows.length;
  }

  async processQueue(): Promise<void> {
    if (!await this.canSubmit()) return;

    // Get next queued job
    const nextJob = await supabase
      .from('cyl_pipeline_runs')
      .select('*')
      .eq('status', 'queued')
      .order('created_at', { ascending: true })
      .limit(1)
      .single();

    if (nextJob) {
      await this.submitJob(nextJob);
    }
  }
}
```

### Argo Workflow Submission

#### Parameterized Workflow

```yaml
# sleap-roots-pipeline-parameterized.yaml
apiVersion: argoproj.io/v1alpha1
kind: Workflow
metadata:
  generateName: sleap-roots-pipeline-
  namespace: runai-tye-lab
  labels:
    project: tye-lab
    managed-by: bloom
spec:
  entrypoint: pipeline
  serviceAccountName: default

  arguments:
    parameters:
    - name: experiment-id
      description: "Bloom experiment UUID"
    - name: pipeline-run-id
      description: "Bloom pipeline_run UUID for provenance"
    - name: species
      description: "Species name (lowercase)"
    - name: min-age
      description: "Minimum plant age (DAG)"
    - name: max-age
      description: "Maximum plant age (DAG)"
    - name: input-path
      description: "Path to input images"
    - name: output-path
      description: "Path for pipeline outputs"
    - name: model-primary
      value: ""
      description: "Override primary model (empty = auto)"
    - name: model-lateral
      value: ""
      description: "Override lateral model (empty = auto)"

  volumes:
  - name: bloom-data
    hostPath:
      path: /hpi/hpi_dev/bloom
      type: Directory

  templates:
  - name: pipeline
    dag:
      tasks:
      - name: models-downloader
        templateRef:
          name: models-downloader-template
          template: models-downloader
        arguments:
          parameters:
          - name: species
            value: "{{workflow.parameters.species}}"
          - name: min-age
            value: "{{workflow.parameters.min-age}}"
          - name: max-age
            value: "{{workflow.parameters.max-age}}"
          - name: model-primary
            value: "{{workflow.parameters.model-primary}}"
          - name: model-lateral
            value: "{{workflow.parameters.model-lateral}}"
          - name: output-path
            value: "{{workflow.parameters.output-path}}/models"

      - name: predictor
        dependencies: [models-downloader]
        templateRef:
          name: sleap-roots-predictor-template
          template: predictor
        arguments:
          parameters:
          - name: input-path
            value: "{{workflow.parameters.input-path}}"
          - name: models-path
            value: "{{workflow.parameters.output-path}}/models"
          - name: output-path
            value: "{{workflow.parameters.output-path}}/predictions"

      - name: trait-extractor
        dependencies: [predictor]
        templateRef:
          name: sleap-roots-trait-extractor-template
          template: trait-extractor
        arguments:
          parameters:
          - name: species
            value: "{{workflow.parameters.species}}"
          - name: predictions-path
            value: "{{workflow.parameters.output-path}}/predictions"
          - name: output-path
            value: "{{workflow.parameters.output-path}}/traits"

      - name: notify-completion
        dependencies: [trait-extractor]
        template: notify
        arguments:
          parameters:
          - name: pipeline-run-id
            value: "{{workflow.parameters.pipeline-run-id}}"
          - name: status
            value: "completed"

  - name: notify
    inputs:
      parameters:
      - name: pipeline-run-id
      - name: status
    container:
      image: curlimages/curl:latest
      command: [curl]
      args:
      - "-X"
      - "POST"
      - "-H"
      - "Content-Type: application/json"
      - "-d"
      - '{"status": "{{inputs.parameters.status}}"}'
      - "http://bloom-backend:3000/api/v1/pipeline/runs/{{inputs.parameters.pipeline-run-id}}/webhook"
```

#### Submission Command

```bash
# Submit workflow with parameters
argo submit sleap-roots-pipeline-parameterized.yaml \
  --namespace runai-tye-lab \
  --parameter experiment-id="$EXPERIMENT_ID" \
  --parameter pipeline-run-id="$PIPELINE_RUN_ID" \
  --parameter species="$SPECIES" \
  --parameter min-age="$MIN_AGE" \
  --parameter max-age="$MAX_AGE" \
  --parameter input-path="/hpi/hpi_dev/bloom/experiments/$EXPERIMENT_ID/images" \
  --parameter output-path="/hpi/hpi_dev/bloom/experiments/$EXPERIMENT_ID/pipeline_outputs/$PIPELINE_RUN_ID" \
  --output name
```

### Backend Service Implementation

```typescript
// src/services/pipeline.service.ts

import { createClient } from '@supabase/supabase-js';
import { exec } from 'child_process';
import { promisify } from 'util';

const execAsync = promisify(exec);

interface TriggerPipelineRequest {
  experimentId: string;
  waves?: number[];
  minAge?: number;
  maxAge?: number;
  modelOverride?: {
    primaryModelId?: string;
    lateralModelId?: string;
  };
  priority?: 'normal' | 'high';
}

interface PipelineRun {
  id: string;
  experimentId: string;
  status: 'queued' | 'submitting' | 'running' | 'completed' | 'failed' | 'cancelled';
  argoWorkflowName?: string;
  argoWorkflowUid?: string;
  config: object;
  triggeredBy: string;
  triggeredAt: string;
  startedAt?: string;
  completedAt?: string;
  errorMessage?: string;
}

export class PipelineService {
  private readonly ARGO_NAMESPACE = process.env.ARGO_NAMESPACE || 'runai-tye-lab';
  private readonly MAX_CONCURRENT = 4;
  private readonly POLL_INTERVAL_MS = 30000;

  private argoEnv = {
    ...process.env,
    ARGO_SERVER: process.env.ARGO_SERVER,
    ARGO_HTTP1: 'true',
    ARGO_SECURE: 'false',
    ARGO_TOKEN: process.env.ARGO_TOKEN,
  };

  /**
   * Trigger a pipeline run for an experiment
   */
  async triggerPipeline(
    request: TriggerPipelineRequest,
    userId: string
  ): Promise<PipelineRun> {
    // 1. Validate experiment exists and user has access
    const experiment = await this.getExperiment(request.experimentId);
    if (!experiment) {
      throw new Error(`Experiment ${request.experimentId} not found`);
    }

    // 2. Check user queue limit
    const userQueuedCount = await this.getUserQueuedCount(userId);
    if (userQueuedCount >= 5) {
      throw new Error('You have reached the maximum queued jobs limit (5)');
    }

    // 3. Determine species and age range
    const species = experiment.species.common_name.toLowerCase();
    const minAge = request.minAge ?? experiment.min_age ?? 2;
    const maxAge = request.maxAge ?? experiment.max_age ?? 14;

    // 4. Create pipeline_run record
    const { data: pipelineRun, error } = await supabase
      .from('cyl_pipeline_runs')
      .insert({
        experiment_id: request.experimentId,
        status: 'queued',
        triggered_by: userId,
        config: {
          waves: request.waves,
          min_age: minAge,
          max_age: maxAge,
          species,
          model_override: request.modelOverride,
          priority: request.priority || 'normal',
        },
      })
      .select()
      .single();

    if (error) throw error;

    // 5. Try to process queue immediately
    this.processQueue().catch(console.error);

    // 6. Return run info with queue position
    const position = await this.getQueuePosition(pipelineRun.id);

    return {
      ...pipelineRun,
      position_in_queue: position,
    };
  }

  /**
   * Process the queue - submit next job if capacity available
   */
  async processQueue(): Promise<void> {
    const runningCount = await this.getRunningCount();

    if (runningCount >= this.MAX_CONCURRENT) {
      console.log(`Queue full: ${runningCount}/${this.MAX_CONCURRENT} running`);
      return;
    }

    // Get next queued job (ordered by priority then created_at)
    const { data: nextJob } = await supabase
      .from('cyl_pipeline_runs')
      .select('*, cyl_experiments(*)')
      .eq('status', 'queued')
      .order('created_at', { ascending: true })
      .limit(1)
      .single();

    if (!nextJob) {
      console.log('No jobs in queue');
      return;
    }

    await this.submitJob(nextJob);
  }

  /**
   * Submit a job to Argo
   */
  private async submitJob(run: PipelineRun): Promise<void> {
    // Update status to submitting
    await supabase
      .from('cyl_pipeline_runs')
      .update({ status: 'submitting' })
      .eq('id', run.id);

    try {
      const config = run.config as any;
      const experiment = run.cyl_experiments;

      // Build paths
      const inputPath = `/hpi/hpi_dev/bloom/experiments/${run.experimentId}/images`;
      const outputPath = `/hpi/hpi_dev/bloom/experiments/${run.experimentId}/pipeline_outputs/${run.id}`;

      // Submit to Argo
      const args = [
        'submit', 'sleap-roots-pipeline-parameterized.yaml',
        '--namespace', this.ARGO_NAMESPACE,
        '--parameter', `experiment-id=${run.experimentId}`,
        '--parameter', `pipeline-run-id=${run.id}`,
        '--parameter', `species=${config.species}`,
        '--parameter', `min-age=${config.min_age}`,
        '--parameter', `max-age=${config.max_age}`,
        '--parameter', `input-path=${inputPath}`,
        '--parameter', `output-path=${outputPath}`,
        '--parameter', `model-primary=${config.model_override?.primaryModelId || ''}`,
        '--parameter', `model-lateral=${config.model_override?.lateralModelId || ''}`,
        '--output', 'name',
      ];

      const result = await execAsync(`argo ${args.join(' ')}`, { env: this.argoEnv });
      const workflowName = result.stdout.trim();

      // Get workflow UID
      const statusResult = await execAsync(
        `argo get ${workflowName} -n ${this.ARGO_NAMESPACE} --output json`,
        { env: this.argoEnv }
      );
      const workflowStatus = JSON.parse(statusResult.stdout);

      // Update record with Argo info
      await supabase
        .from('cyl_pipeline_runs')
        .update({
          status: 'running',
          argo_workflow_name: workflowName,
          argo_workflow_uid: workflowStatus.metadata.uid,
          started_at: new Date().toISOString(),
        })
        .eq('id', run.id);

      // Start status polling
      this.startStatusPolling(run.id, workflowName);

    } catch (error) {
      console.error('Failed to submit job:', error);
      await supabase
        .from('cyl_pipeline_runs')
        .update({
          status: 'failed',
          error_message: error.message,
        })
        .eq('id', run.id);

      // Try next job in queue
      this.processQueue().catch(console.error);
    }
  }

  /**
   * Poll Argo for workflow status
   */
  private async startStatusPolling(runId: string, workflowName: string): Promise<void> {
    const poll = async () => {
      try {
        const result = await execAsync(
          `argo get ${workflowName} -n ${this.ARGO_NAMESPACE} --output json`,
          { env: this.argoEnv }
        );
        const status = JSON.parse(result.stdout);
        const phase = status.status?.phase;

        // Update database with current status
        await supabase
          .from('cyl_pipeline_runs')
          .update({
            metadata: {
              phase,
              started_at: status.status?.startedAt,
              finished_at: status.status?.finishedAt,
              nodes: this.summarizeNodes(status.status?.nodes),
            },
          })
          .eq('id', runId);

        if (phase === 'Succeeded') {
          await this.onPipelineComplete(runId);
        } else if (phase === 'Failed' || phase === 'Error') {
          await this.onPipelineFailed(runId, status);
        } else {
          // Continue polling
          setTimeout(poll, this.POLL_INTERVAL_MS);
        }
      } catch (error) {
        console.error('Polling error:', error);
        setTimeout(poll, this.POLL_INTERVAL_MS);
      }
    };

    poll();
  }

  /**
   * Handle successful pipeline completion
   */
  private async onPipelineComplete(runId: string): Promise<void> {
    await supabase
      .from('cyl_pipeline_runs')
      .update({
        status: 'completed',
        completed_at: new Date().toISOString(),
      })
      .eq('id', runId);

    // Trigger results sync (Issue #4)
    // await this.syncResults(runId);

    // Trigger Box backup
    // await this.triggerBoxBackup(runId);

    // Process next job in queue
    this.processQueue().catch(console.error);
  }

  /**
   * Handle pipeline failure
   */
  private async onPipelineFailed(runId: string, status: any): Promise<void> {
    const failedNodes = Object.values(status.status?.nodes || {})
      .filter((n: any) => n.phase === 'Failed')
      .map((n: any) => n.message);

    await supabase
      .from('cyl_pipeline_runs')
      .update({
        status: 'failed',
        completed_at: new Date().toISOString(),
        error_message: failedNodes.join('; ') || 'Unknown error',
      })
      .eq('id', runId);

    // Process next job in queue
    this.processQueue().catch(console.error);
  }

  /**
   * Cancel a running or queued pipeline
   */
  async cancelPipeline(runId: string, userId: string): Promise<void> {
    const run = await this.getPipelineRun(runId);

    // Check ownership or admin
    if (run.triggered_by !== userId && !await this.isAdmin(userId)) {
      throw new Error('Not authorized to cancel this pipeline');
    }

    if (run.status === 'running' && run.argo_workflow_name) {
      // Stop Argo workflow
      await execAsync(
        `argo stop ${run.argo_workflow_name} -n ${this.ARGO_NAMESPACE}`,
        { env: this.argoEnv }
      );
    }

    await supabase
      .from('cyl_pipeline_runs')
      .update({
        status: 'cancelled',
        completed_at: new Date().toISOString(),
      })
      .eq('id', runId);

    // Process next job in queue
    this.processQueue().catch(console.error);
  }

  /**
   * Get running job count
   */
  private async getRunningCount(): Promise<number> {
    const result = await execAsync(
      `argo list -n ${this.ARGO_NAMESPACE} --running -l managed-by=bloom --output json`,
      { env: this.argoEnv }
    );
    const workflows = JSON.parse(result.stdout || '[]');
    return workflows.length;
  }

  /**
   * Get queue position for a job
   */
  private async getQueuePosition(runId: string): Promise<number> {
    const { data: queuedJobs } = await supabase
      .from('cyl_pipeline_runs')
      .select('id')
      .eq('status', 'queued')
      .order('created_at', { ascending: true });

    const position = queuedJobs?.findIndex(j => j.id === runId) ?? -1;
    return position + 1; // 1-indexed position
  }
}
```

### CLI Commands

```bash
# bloom-cli/src/commands/pipeline.ts

# List experiments
bloom pipeline experiments list

# Trigger pipeline
bloom pipeline run --experiment-id <uuid> [--waves 1,2,3] [--ages 2-14]

# Check status
bloom pipeline status <run-id>

# List runs
bloom pipeline runs [--experiment-id <uuid>] [--status running|queued|completed]

# Cancel run
bloom pipeline cancel <run-id>

# View logs
bloom pipeline logs <run-id> [--follow]
```

---

## Database Schema Changes

```sql
-- Migration: create_pipeline_runs_table

CREATE TABLE cyl_pipeline_runs (
  id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  experiment_id UUID NOT NULL REFERENCES cyl_experiments(id) ON DELETE CASCADE,
  status TEXT NOT NULL DEFAULT 'queued'
    CHECK (status IN ('queued', 'submitting', 'running', 'completed', 'failed', 'cancelled')),

  -- Argo workflow info
  argo_workflow_name TEXT,
  argo_workflow_uid TEXT,

  -- Configuration
  config JSONB NOT NULL DEFAULT '{}',
  -- config schema:
  -- {
  --   "waves": [1, 2, 3],
  --   "min_age": 2,
  --   "max_age": 14,
  --   "species": "soybean",
  --   "model_override": { "primary_model_id": "...", "lateral_model_id": "..." },
  --   "priority": "normal"
  -- }

  -- Execution metadata (from Argo)
  metadata JSONB,

  -- User tracking
  triggered_by UUID REFERENCES auth.users(id),
  triggered_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

  -- Timestamps
  started_at TIMESTAMPTZ,
  completed_at TIMESTAMPTZ,

  -- Error info
  error_message TEXT,

  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Indexes
CREATE INDEX idx_pipeline_runs_experiment ON cyl_pipeline_runs(experiment_id);
CREATE INDEX idx_pipeline_runs_status ON cyl_pipeline_runs(status);
CREATE INDEX idx_pipeline_runs_triggered_by ON cyl_pipeline_runs(triggered_by);
CREATE INDEX idx_pipeline_runs_created_at ON cyl_pipeline_runs(created_at);

-- Updated at trigger
CREATE TRIGGER update_pipeline_runs_updated_at
  BEFORE UPDATE ON cyl_pipeline_runs
  FOR EACH ROW
  EXECUTE FUNCTION update_updated_at_column();

-- RLS policies
ALTER TABLE cyl_pipeline_runs ENABLE ROW LEVEL SECURITY;

-- Users can view their own runs
CREATE POLICY "Users can view own pipeline runs"
  ON cyl_pipeline_runs FOR SELECT
  USING (triggered_by = auth.uid());

-- Users can create runs for experiments they have access to
CREATE POLICY "Users can create pipeline runs"
  ON cyl_pipeline_runs FOR INSERT
  WITH CHECK (
    EXISTS (
      SELECT 1 FROM cyl_experiments e
      WHERE e.id = experiment_id
      AND (e.scientist_id = auth.uid() OR is_admin(auth.uid()))
    )
  );

-- Only admins or owners can cancel
CREATE POLICY "Users can update own pipeline runs"
  ON cyl_pipeline_runs FOR UPDATE
  USING (triggered_by = auth.uid() OR is_admin(auth.uid()));
```

---

## Tasks

### Phase 1: Backend API

- [ ] **2.1** Create pipeline service with queue management
- [ ] **2.2** Implement `/api/v1/pipeline/trigger` endpoint
- [ ] **2.3** Implement `/api/v1/pipeline/runs/{id}` status endpoint
- [ ] **2.4** Implement `/api/v1/pipeline/runs` list endpoint
- [ ] **2.5** Implement `/api/v1/pipeline/runs/{id}/cancel` endpoint
- [ ] **2.6** Implement `/api/v1/pipeline/runs/{id}/logs` endpoint
- [ ] **2.7** Add status polling background job
- [ ] **2.8** Add queue processor cron job (every 30 seconds)

### Phase 2: Argo Integration

- [ ] **2.9** Create parameterized workflow template
- [ ] **2.10** Update WorkflowTemplates with bloom labels
- [ ] **2.11** Add notification step for completion webhook
- [ ] **2.12** Test submission with sample experiment
- [ ] **2.13** Test cancellation flow

### Phase 3: CLI

- [ ] **2.14** Add `bloom pipeline run` command
- [ ] **2.15** Add `bloom pipeline status` command
- [ ] **2.16** Add `bloom pipeline runs` list command
- [ ] **2.17** Add `bloom pipeline cancel` command
- [ ] **2.18** Add `bloom pipeline logs` command

### Phase 4: Basic UI

- [ ] **2.19** Add "Run Pipeline" button to experiment detail page
- [ ] **2.20** Add pipeline status indicator
- [ ] **2.21** Add cancel button for running/queued jobs
- [ ] **2.22** Show queue position for queued jobs

### Phase 5: Testing

- [ ] **2.23** Unit tests for queue management logic
- [ ] **2.24** Integration tests for Argo submission
- [ ] **2.25** E2E test: trigger → complete → verify status
- [ ] **2.26** Load test: submit 20 jobs, verify queue behavior

---

## Acceptance Criteria

- [ ] API endpoint accepts pipeline trigger request and returns run ID
- [ ] Jobs are queued when max concurrent limit reached
- [ ] Queue position visible to user
- [ ] Running jobs show real-time status from Argo
- [ ] Completed jobs marked as completed with timestamp
- [ ] Failed jobs capture error message
- [ ] Users can cancel their own jobs
- [ ] Admins can cancel any job
- [ ] CLI commands work for all operations
- [ ] Basic UI button triggers pipeline

---

## Future Enhancements (Out of Scope)

- Automatic triggering based on experiment completion (separate issue)
- Slack/email notifications
- Retry failed jobs with higher memory
- Priority queue for urgent jobs

---

## Labels

`backend`, `api`, `argo`, `P0`

## Assignees

- Backend API: TBD
- Argo Integration: TBD
- CLI: TBD
