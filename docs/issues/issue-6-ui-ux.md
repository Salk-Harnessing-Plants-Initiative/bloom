# Issue 6: UI/UX - Prediction Viewer & Status Dashboard

**EPIC**: Automated Root Trait Extraction Pipeline Integration
**Priority**: P1
**Dependencies**: Issue #4 (Results Sync)
**Blocks**: None

---

## Summary

Build user interface components for pipeline status monitoring, prediction visualization, and results exploration in the Bloom web application.

## Background

Scientists need to:
1. Monitor pipeline execution status
2. View predictions overlaid on root images (like sleap-roots viewer)
3. Explore extracted traits with filtering and export
4. Access Box backup links and analysis outputs
5. Compare predictions across pipeline runs (different models)

## Goals

1. Pipeline status dashboard with real-time updates
2. Interactive prediction viewer with image overlay
3. Trait exploration interface
4. Analysis results viewer (heritability, visualizations)
5. Integration with existing Bloom experiment views

## Technical Design

### Component Architecture

```
┌─────────────────────────────────────────────────────────────────────────┐
│                         Bloom Web Application                            │
│                                                                          │
│  ┌─────────────────────────────────────────────────────────────────┐    │
│  │                     Experiment Detail Page                       │    │
│  │  ┌─────────────┐  ┌─────────────┐  ┌─────────────────────────┐  │    │
│  │  │  Overview   │  │   Scans     │  │  Pipeline Runs          │  │    │
│  │  │  Tab        │  │   Tab       │  │  Tab (NEW)              │  │    │
│  │  └─────────────┘  └─────────────┘  └─────────────────────────┘  │    │
│  └─────────────────────────────────────────────────────────────────┘    │
│                                                                          │
│  ┌─────────────────────────────────────────────────────────────────┐    │
│  │                   Pipeline Runs Tab Content                      │    │
│  │                                                                   │    │
│  │  ┌──────────────────────┐  ┌───────────────────────────────┐    │    │
│  │  │  Run Pipeline Button │  │  Pipeline Status Dashboard    │    │    │
│  │  │  (triggers modal)    │  │  - Active runs list           │    │    │
│  │  └──────────────────────┘  │  - Progress bars              │    │    │
│  │                             │  - Status badges              │    │    │
│  │                             └───────────────────────────────┘    │    │
│  │                                                                   │    │
│  │  ┌───────────────────────────────────────────────────────────┐  │    │
│  │  │                   Completed Runs Table                     │  │    │
│  │  │  Run ID | Status | Traits | Predictions | Box Link | Date │  │    │
│  │  └───────────────────────────────────────────────────────────┘  │    │
│  └─────────────────────────────────────────────────────────────────┘    │
│                                                                          │
│  ┌─────────────────────────────────────────────────────────────────┐    │
│  │                    Prediction Viewer (Modal)                     │    │
│  │  ┌─────────────────────────┐  ┌────────────────────────────┐    │    │
│  │  │                         │  │  Scan Info                 │    │    │
│  │  │    Image Canvas         │  │  - QR Code                 │    │    │
│  │  │    with Overlay         │  │  - Age, Wave               │    │    │
│  │  │                         │  │  - Traits                  │    │    │
│  │  │  [Frame Navigation]     │  │                            │    │    │
│  │  │  ◀ Frame 1/4 ▶          │  │  [Version Selector]        │    │    │
│  │  └─────────────────────────┘  └────────────────────────────┘    │    │
│  └─────────────────────────────────────────────────────────────────┘    │
└─────────────────────────────────────────────────────────────────────────┘
```

### Pipeline Status Dashboard

```tsx
// components/pipeline/PipelineStatusDashboard.tsx

import { useQuery } from '@tanstack/react-query';
import { Badge, Progress, Card } from '@/components/ui';

interface PipelineRun {
  id: string;
  status: 'queued' | 'running' | 'completed' | 'failed';
  phase?: string;
  progress?: {
    models_downloader: string;
    predictor: string;
    trait_extractor: string;
    results_sync: string;
  };
  startedAt?: string;
  estimatedCompletion?: string;
  scanCount?: number;
  scansProcessed?: number;
}

export function PipelineStatusDashboard({ experimentId }: { experimentId: string }) {
  const { data: runs, isLoading } = useQuery({
    queryKey: ['pipeline-runs', experimentId],
    queryFn: () => fetchPipelineRuns(experimentId),
    refetchInterval: 5000, // Poll every 5 seconds for active runs
  });

  const activeRuns = runs?.filter(r => ['queued', 'running'].includes(r.status)) || [];
  const completedRuns = runs?.filter(r => ['completed', 'failed'].includes(r.status)) || [];

  return (
    <div className="space-y-6">
      {/* Trigger Button */}
      <div className="flex justify-between items-center">
        <h2 className="text-xl font-semibold">Pipeline Runs</h2>
        <TriggerPipelineButton experimentId={experimentId} />
      </div>

      {/* Active Runs */}
      {activeRuns.length > 0 && (
        <Card>
          <h3 className="font-medium mb-4">Active Runs</h3>
          {activeRuns.map(run => (
            <ActiveRunCard key={run.id} run={run} />
          ))}
        </Card>
      )}

      {/* Completed Runs Table */}
      <Card>
        <h3 className="font-medium mb-4">Run History</h3>
        <CompletedRunsTable runs={completedRuns} />
      </Card>
    </div>
  );
}

function ActiveRunCard({ run }: { run: PipelineRun }) {
  const steps = ['models_downloader', 'predictor', 'trait_extractor', 'results_sync'];
  const completedSteps = steps.filter(s => run.progress?.[s] === 'completed').length;
  const progress = (completedSteps / steps.length) * 100;

  return (
    <div className="border rounded-lg p-4 mb-4">
      <div className="flex justify-between items-center mb-2">
        <span className="font-mono text-sm">{run.id.slice(0, 8)}</span>
        <Badge variant={run.status === 'running' ? 'primary' : 'secondary'}>
          {run.status}
        </Badge>
      </div>

      <Progress value={progress} className="mb-2" />

      <div className="grid grid-cols-4 gap-2 text-xs">
        {steps.map(step => (
          <StepIndicator
            key={step}
            name={step.replace('_', ' ')}
            status={run.progress?.[step] || 'pending'}
          />
        ))}
      </div>

      {run.estimatedCompletion && (
        <p className="text-sm text-muted-foreground mt-2">
          Estimated completion: {formatTime(run.estimatedCompletion)}
        </p>
      )}

      <div className="mt-2 flex gap-2">
        <Button variant="outline" size="sm" onClick={() => viewLogs(run.id)}>
          View Logs
        </Button>
        {run.status !== 'completed' && (
          <Button variant="destructive" size="sm" onClick={() => cancelRun(run.id)}>
            Cancel
          </Button>
        )}
      </div>
    </div>
  );
}

function CompletedRunsTable({ runs }: { runs: PipelineRun[] }) {
  return (
    <table className="w-full">
      <thead>
        <tr className="text-left text-sm text-muted-foreground">
          <th className="pb-2">Run ID</th>
          <th className="pb-2">Status</th>
          <th className="pb-2">Traits</th>
          <th className="pb-2">Predictions</th>
          <th className="pb-2">Box Link</th>
          <th className="pb-2">Date</th>
          <th className="pb-2">Actions</th>
        </tr>
      </thead>
      <tbody>
        {runs.map(run => (
          <tr key={run.id} className="border-t">
            <td className="py-2 font-mono text-sm">{run.id.slice(0, 8)}</td>
            <td>
              <StatusBadge status={run.status} />
            </td>
            <td>{run.traitsCount || '-'}</td>
            <td>{run.predictionsCount || '-'}</td>
            <td>
              {run.boxLink ? (
                <a href={run.boxLink} target="_blank" className="text-blue-600 hover:underline">
                  Open
                </a>
              ) : '-'}
            </td>
            <td className="text-sm text-muted-foreground">
              {formatDate(run.completedAt)}
            </td>
            <td>
              <DropdownMenu>
                <DropdownMenuItem onClick={() => viewTraits(run.id)}>
                  View Traits
                </DropdownMenuItem>
                <DropdownMenuItem onClick={() => viewPredictions(run.id)}>
                  View Predictions
                </DropdownMenuItem>
                <DropdownMenuItem onClick={() => viewMetadata(run.id)}>
                  View Metadata
                </DropdownMenuItem>
                {run.analysisRunId && (
                  <DropdownMenuItem onClick={() => viewAnalysis(run.id)}>
                    View Analysis
                  </DropdownMenuItem>
                )}
              </DropdownMenu>
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
```

### Prediction Viewer Component

```tsx
// components/pipeline/PredictionViewer.tsx

import { useState, useRef, useEffect } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Canvas, Image, Line, Circle } from '@/components/canvas';

interface PredictionViewerProps {
  scanId: string;
  pipelineRunId?: string; // Optional: specific run, default to latest
}

interface Prediction {
  imageId: string;
  frameNumber: number;
  objectPath: string;
  landmarks: Landmark[];
}

interface Landmark {
  type: 'primary' | 'lateral' | 'crown';
  points: { x: number; y: number }[];
}

export function PredictionViewer({ scanId, pipelineRunId }: PredictionViewerProps) {
  const [currentFrame, setCurrentFrame] = useState(0);
  const [showOverlay, setShowOverlay] = useState(true);
  const [selectedVersion, setSelectedVersion] = useState(pipelineRunId);
  const canvasRef = useRef<HTMLCanvasElement>(null);

  // Fetch available versions for this scan
  const { data: versions } = useQuery({
    queryKey: ['prediction-versions', scanId],
    queryFn: () => fetchPredictionVersions(scanId),
  });

  // Fetch predictions for selected version
  const { data: predictions } = useQuery({
    queryKey: ['predictions', scanId, selectedVersion],
    queryFn: () => fetchPredictions(scanId, selectedVersion),
    enabled: !!selectedVersion,
  });

  // Fetch scan images
  const { data: images } = useQuery({
    queryKey: ['scan-images', scanId],
    queryFn: () => fetchScanImages(scanId),
  });

  // Fetch scan traits
  const { data: traits } = useQuery({
    queryKey: ['scan-traits', scanId, selectedVersion],
    queryFn: () => fetchScanTraits(scanId, selectedVersion),
  });

  const currentImage = images?.[currentFrame];
  const currentPrediction = predictions?.find(p => p.frameNumber === currentFrame);

  // Draw image and overlay
  useEffect(() => {
    if (!canvasRef.current || !currentImage) return;

    const ctx = canvasRef.current.getContext('2d');
    const img = new window.Image();
    img.src = `/api/images/${currentImage.id}`;

    img.onload = () => {
      ctx.clearRect(0, 0, canvasRef.current.width, canvasRef.current.height);
      ctx.drawImage(img, 0, 0);

      if (showOverlay && currentPrediction) {
        drawPredictionOverlay(ctx, currentPrediction.landmarks);
      }
    };
  }, [currentImage, currentPrediction, showOverlay]);

  return (
    <div className="flex gap-6">
      {/* Image Canvas */}
      <div className="flex-1">
        <div className="relative">
          <canvas
            ref={canvasRef}
            width={800}
            height={600}
            className="border rounded-lg"
          />

          {/* Frame Navigation */}
          <div className="absolute bottom-4 left-1/2 transform -translate-x-1/2 bg-black/70 text-white px-4 py-2 rounded-full flex items-center gap-4">
            <button
              onClick={() => setCurrentFrame(f => Math.max(0, f - 1))}
              disabled={currentFrame === 0}
            >
              ◀
            </button>
            <span>Frame {currentFrame + 1} / {images?.length || 0}</span>
            <button
              onClick={() => setCurrentFrame(f => Math.min((images?.length || 1) - 1, f + 1))}
              disabled={currentFrame >= (images?.length || 1) - 1}
            >
              ▶
            </button>
          </div>
        </div>

        {/* Overlay Toggle */}
        <div className="mt-4 flex items-center gap-2">
          <input
            type="checkbox"
            id="show-overlay"
            checked={showOverlay}
            onChange={e => setShowOverlay(e.target.checked)}
          />
          <label htmlFor="show-overlay">Show prediction overlay</label>
        </div>
      </div>

      {/* Info Panel */}
      <div className="w-80 space-y-4">
        {/* Version Selector */}
        <div>
          <label className="text-sm font-medium">Pipeline Run</label>
          <select
            value={selectedVersion}
            onChange={e => setSelectedVersion(e.target.value)}
            className="w-full mt-1 border rounded p-2"
          >
            {versions?.map(v => (
              <option key={v.id} value={v.id}>
                {v.version} - {formatDate(v.createdAt)}
              </option>
            ))}
          </select>
        </div>

        {/* Scan Info */}
        <div className="border rounded-lg p-4">
          <h4 className="font-medium mb-2">Scan Information</h4>
          <dl className="space-y-1 text-sm">
            <div className="flex justify-between">
              <dt className="text-muted-foreground">QR Code</dt>
              <dd>{currentImage?.qrCode}</dd>
            </div>
            <div className="flex justify-between">
              <dt className="text-muted-foreground">Plant Age</dt>
              <dd>{currentImage?.plantAgeDays} days</dd>
            </div>
            <div className="flex justify-between">
              <dt className="text-muted-foreground">Wave</dt>
              <dd>{currentImage?.waveNumber}</dd>
            </div>
          </dl>
        </div>

        {/* Extracted Traits */}
        <div className="border rounded-lg p-4">
          <h4 className="font-medium mb-2">Extracted Traits</h4>
          <dl className="space-y-1 text-sm max-h-60 overflow-y-auto">
            {traits?.map(trait => (
              <div key={trait.name} className="flex justify-between">
                <dt className="text-muted-foreground truncate" title={trait.name}>
                  {trait.name}
                </dt>
                <dd className="font-mono">{trait.value.toFixed(2)}</dd>
              </div>
            ))}
          </dl>
        </div>

        {/* Legend */}
        <div className="border rounded-lg p-4">
          <h4 className="font-medium mb-2">Legend</h4>
          <div className="space-y-1 text-sm">
            <div className="flex items-center gap-2">
              <span className="w-4 h-1 bg-blue-500"></span>
              <span>Primary root</span>
            </div>
            <div className="flex items-center gap-2">
              <span className="w-4 h-1 bg-green-500"></span>
              <span>Lateral roots</span>
            </div>
            <div className="flex items-center gap-2">
              <span className="w-4 h-1 bg-orange-500"></span>
              <span>Crown roots</span>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

function drawPredictionOverlay(ctx: CanvasRenderingContext2D, landmarks: Landmark[]) {
  const colors = {
    primary: '#3B82F6',   // blue
    lateral: '#22C55E',   // green
    crown: '#F97316',     // orange
  };

  for (const landmark of landmarks) {
    const color = colors[landmark.type];
    ctx.strokeStyle = color;
    ctx.lineWidth = 2;

    // Draw connected points
    ctx.beginPath();
    landmark.points.forEach((point, i) => {
      if (i === 0) {
        ctx.moveTo(point.x, point.y);
      } else {
        ctx.lineTo(point.x, point.y);
      }
    });
    ctx.stroke();

    // Draw landmark points
    ctx.fillStyle = color;
    for (const point of landmark.points) {
      ctx.beginPath();
      ctx.arc(point.x, point.y, 3, 0, Math.PI * 2);
      ctx.fill();
    }
  }
}
```

### Trigger Pipeline Modal

```tsx
// components/pipeline/TriggerPipelineModal.tsx

import { useState } from 'react';
import { useMutation, useQuery } from '@tanstack/react-query';
import { Dialog, DialogContent, DialogHeader } from '@/components/ui/dialog';

interface TriggerPipelineModalProps {
  experimentId: string;
  open: boolean;
  onClose: () => void;
}

export function TriggerPipelineModal({ experimentId, open, onClose }: TriggerPipelineModalProps) {
  const [config, setConfig] = useState({
    waves: [],
    minAge: null,
    maxAge: null,
    runAnalysis: true,
  });

  const { data: experiment } = useQuery({
    queryKey: ['experiment', experimentId],
    queryFn: () => fetchExperiment(experimentId),
  });

  const triggerMutation = useMutation({
    mutationFn: (config) => triggerPipeline(experimentId, config),
    onSuccess: (data) => {
      toast.success(`Pipeline run ${data.id.slice(0, 8)} started!`);
      onClose();
    },
    onError: (error) => {
      toast.error(`Failed to start pipeline: ${error.message}`);
    },
  });

  return (
    <Dialog open={open} onOpenChange={onClose}>
      <DialogContent className="max-w-md">
        <DialogHeader>
          <h2 className="text-lg font-semibold">Run Trait Extraction Pipeline</h2>
          <p className="text-sm text-muted-foreground">
            {experiment?.name}
          </p>
        </DialogHeader>

        <form onSubmit={(e) => { e.preventDefault(); triggerMutation.mutate(config); }}>
          {/* Wave Selection */}
          <div className="mb-4">
            <label className="text-sm font-medium">Waves</label>
            <p className="text-xs text-muted-foreground mb-2">
              Select specific waves or leave empty for all
            </p>
            <div className="flex flex-wrap gap-2">
              {experiment?.waves?.map(wave => (
                <label key={wave.id} className="flex items-center gap-1">
                  <input
                    type="checkbox"
                    checked={config.waves.includes(wave.number)}
                    onChange={(e) => {
                      if (e.target.checked) {
                        setConfig(c => ({ ...c, waves: [...c.waves, wave.number] }));
                      } else {
                        setConfig(c => ({ ...c, waves: c.waves.filter(w => w !== wave.number) }));
                      }
                    }}
                  />
                  <span className="text-sm">Wave {wave.number}</span>
                </label>
              ))}
            </div>
          </div>

          {/* Age Range */}
          <div className="mb-4 grid grid-cols-2 gap-4">
            <div>
              <label className="text-sm font-medium">Min Age (DAG)</label>
              <input
                type="number"
                className="w-full mt-1 border rounded p-2"
                placeholder="Auto"
                value={config.minAge || ''}
                onChange={(e) => setConfig(c => ({ ...c, minAge: e.target.value ? parseInt(e.target.value) : null }))}
              />
            </div>
            <div>
              <label className="text-sm font-medium">Max Age (DAG)</label>
              <input
                type="number"
                className="w-full mt-1 border rounded p-2"
                placeholder="Auto"
                value={config.maxAge || ''}
                onChange={(e) => setConfig(c => ({ ...c, maxAge: e.target.value ? parseInt(e.target.value) : null }))}
              />
            </div>
          </div>

          {/* Analysis Option */}
          <div className="mb-6">
            <label className="flex items-center gap-2">
              <input
                type="checkbox"
                checked={config.runAnalysis}
                onChange={(e) => setConfig(c => ({ ...c, runAnalysis: e.target.checked }))}
              />
              <span className="text-sm font-medium">Run QC & heritability analysis</span>
            </label>
            <p className="text-xs text-muted-foreground ml-6">
              Includes outlier detection and publication-ready visualizations
            </p>
          </div>

          {/* Summary */}
          <div className="mb-6 p-3 bg-muted rounded-lg text-sm">
            <p><strong>Species:</strong> {experiment?.species?.common_name}</p>
            <p><strong>Total scans:</strong> {experiment?.scanCount}</p>
            <p><strong>Estimated time:</strong> ~{Math.ceil(experiment?.scanCount / 100 * 15)} minutes</p>
          </div>

          {/* Actions */}
          <div className="flex justify-end gap-2">
            <Button variant="outline" onClick={onClose}>
              Cancel
            </Button>
            <Button type="submit" disabled={triggerMutation.isPending}>
              {triggerMutation.isPending ? 'Starting...' : 'Start Pipeline'}
            </Button>
          </div>
        </form>
      </DialogContent>
    </Dialog>
  );
}
```

### Analysis Results Viewer

```tsx
// components/pipeline/AnalysisResultsViewer.tsx

export function AnalysisResultsViewer({ pipelineRunId }: { pipelineRunId: string }) {
  const { data: analysis } = useQuery({
    queryKey: ['analysis', pipelineRunId],
    queryFn: () => fetchAnalysisResults(pipelineRunId),
  });

  if (!analysis) {
    return <p className="text-muted-foreground">No analysis results available</p>;
  }

  return (
    <div className="space-y-6">
      {/* QC Summary */}
      <Card>
        <h3 className="font-medium mb-4">Quality Control Summary</h3>
        <div className="grid grid-cols-4 gap-4 text-center">
          <div>
            <p className="text-2xl font-bold">{analysis.qcMetrics.inputSamples}</p>
            <p className="text-sm text-muted-foreground">Input Samples</p>
          </div>
          <div>
            <p className="text-2xl font-bold">{analysis.qcMetrics.outputSamples}</p>
            <p className="text-sm text-muted-foreground">Passed QC</p>
          </div>
          <div>
            <p className="text-2xl font-bold text-red-500">{analysis.qcMetrics.removedOutliers}</p>
            <p className="text-sm text-muted-foreground">Outliers Removed</p>
          </div>
          <div>
            <p className="text-2xl font-bold text-green-500">{analysis.qcMetrics.passedHeritability}</p>
            <p className="text-sm text-muted-foreground">High H² Traits</p>
          </div>
        </div>
      </Card>

      {/* Heritability Table */}
      <Card>
        <h3 className="font-medium mb-4">Trait Heritability</h3>
        <HeritabilityTable traits={analysis.heritability} />
      </Card>

      {/* Visualizations Gallery */}
      <Card>
        <h3 className="font-medium mb-4">Visualizations</h3>
        <div className="grid grid-cols-3 gap-4">
          {analysis.visualizations.map(viz => (
            <VizThumbnail key={viz.id} viz={viz} />
          ))}
        </div>
      </Card>
    </div>
  );
}

function HeritabilityTable({ traits }) {
  const [sortBy, setSortBy] = useState('heritability');
  const [filter, setFilter] = useState('all');

  const sortedTraits = [...traits].sort((a, b) => {
    if (sortBy === 'heritability') return b.heritability - a.heritability;
    if (sortBy === 'name') return a.traitName.localeCompare(b.traitName);
    return 0;
  });

  const filteredTraits = sortedTraits.filter(t => {
    if (filter === 'passed') return t.passedThreshold;
    if (filter === 'failed') return !t.passedThreshold;
    return true;
  });

  return (
    <div>
      <div className="flex gap-4 mb-4">
        <select value={filter} onChange={e => setFilter(e.target.value)}>
          <option value="all">All traits</option>
          <option value="passed">Passed threshold</option>
          <option value="failed">Below threshold</option>
        </select>
      </div>

      <table className="w-full text-sm">
        <thead>
          <tr className="text-left border-b">
            <th className="pb-2 cursor-pointer" onClick={() => setSortBy('name')}>
              Trait Name {sortBy === 'name' && '↓'}
            </th>
            <th className="pb-2 cursor-pointer" onClick={() => setSortBy('heritability')}>
              H² {sortBy === 'heritability' && '↓'}
            </th>
            <th className="pb-2">p-value</th>
            <th className="pb-2">Status</th>
          </tr>
        </thead>
        <tbody>
          {filteredTraits.map(trait => (
            <tr key={trait.traitName} className="border-b">
              <td className="py-2">{trait.traitName}</td>
              <td className="py-2 font-mono">{trait.heritability.toFixed(3)}</td>
              <td className="py-2 font-mono">{trait.pValue?.toExponential(2) || '-'}</td>
              <td className="py-2">
                {trait.passedThreshold ? (
                  <Badge variant="success">Passed</Badge>
                ) : (
                  <Badge variant="secondary">Below threshold</Badge>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
```

---

## Tasks

### Phase 1: Pipeline Status Dashboard

- [ ] **6.1** Create PipelineStatusDashboard component
- [ ] **6.2** Implement real-time status polling
- [ ] **6.3** Create ActiveRunCard with progress visualization
- [ ] **6.4** Create CompletedRunsTable with actions
- [ ] **6.5** Add to Experiment detail page as new tab

### Phase 2: Trigger Pipeline Modal

- [ ] **6.6** Create TriggerPipelineModal component
- [ ] **6.7** Implement wave selection UI
- [ ] **6.8** Implement age range inputs
- [ ] **6.9** Add analysis toggle option
- [ ] **6.10** Wire up to pipeline trigger API

### Phase 3: Prediction Viewer

- [ ] **6.11** Create PredictionViewer component
- [ ] **6.12** Implement image loading and display
- [ ] **6.13** Implement prediction overlay rendering
- [ ] **6.14** Add frame navigation
- [ ] **6.15** Add version selector for comparing runs
- [ ] **6.16** Display extracted traits panel

### Phase 4: Analysis Results Viewer

- [ ] **6.17** Create AnalysisResultsViewer component
- [ ] **6.18** Create QC summary cards
- [ ] **6.19** Create HeritabilityTable with sorting/filtering
- [ ] **6.20** Create visualization gallery
- [ ] **6.21** Add visualization lightbox/modal

### Phase 5: Integration

- [ ] **6.22** Add "Pipeline Runs" tab to experiment page
- [ ] **6.23** Add prediction viewer to scan detail view
- [ ] **6.24** Add Box link display throughout UI
- [ ] **6.25** Add pipeline status to experiment list view
- [ ] **6.26** Add notification for pipeline completion

### Phase 6: Testing

- [ ] **6.27** Test status dashboard with active runs
- [ ] **6.28** Test trigger modal submission
- [ ] **6.29** Test prediction viewer with overlays
- [ ] **6.30** Test analysis results display
- [ ] **6.31** Cross-browser testing

---

## Acceptance Criteria

- [ ] Pipeline runs visible in experiment detail page
- [ ] Real-time status updates for running pipelines
- [ ] Users can trigger pipelines via modal
- [ ] Prediction overlay visible on scan images
- [ ] Frame navigation works smoothly
- [ ] Version selector allows comparing predictions
- [ ] Extracted traits displayed alongside images
- [ ] Heritability results viewable and sortable
- [ ] Visualizations accessible in gallery view
- [ ] Box links clickable and open correctly

---

## Design Considerations

- **Accessibility**: Ensure keyboard navigation for frame controls
- **Performance**: Lazy load images in prediction viewer
- **Responsiveness**: Dashboard should work on tablet screens
- **Error States**: Show helpful messages when data unavailable
- **Loading States**: Use skeletons while data loads

---

## Labels

`frontend`, `ui`, `P1`

## Assignees

- UI Development: TBD
- Design Review: TBD
