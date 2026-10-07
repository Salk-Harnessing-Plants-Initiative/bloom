# Argo workflows

Bloom runs its long pipeline jobs as Argo workflows on Salk's Run:ai cluster, in the
`runai-busch-lab` namespace. This page covers the RNA-seq Cell Ranger workflow: how a run starts,
where its result ends up, and how to start one by hand. The pipeline's own steps, images and exit
codes are in [argo/scrna/README.md](../../argo/scrna/README.md).

Update this page whenever you change how a Cell Ranger run is started or where its result goes.

## How a run normally starts

A scientist fills in the RNA-seq form on Bloom (sample, reference, dataset name, species and the
dataset's details). Bloom records the run, the workflows service submits it to Argo, and the
status poller follows it and shows each step on the run page.

| Piece | Where |
|---|---|
| The pipeline (WorkflowTemplate) | [argo/scrna/cellranger/cellranger-count-template.yaml](../../argo/scrna/cellranger/cellranger-count-template.yaml) |
| Submitting a run | `rnaseq-worker` in [services/workflows/](../../services/workflows/README.md) |
| Following a run, linking its dataset | `rnaseq-status-poller` in [services/workflows/](../../services/workflows/README.md) |
| The run page | `/app/timeline/rnaseq/<run id>` |

Prod and staging share the namespace, so each has its own copy of the template:
`cellranger-count-template` (prod) and `cellranger-count-template-staging` (staging).

## Where the result goes

Each sample runs as one chain of steps on a shared folder on the cluster:

```
stage → qc → count → preprocess → cluster → build-h5ad → load-dataset → cleanup
```

- The reads come from S3, and S3 is only read: the reference, an SRA import, or the scientist's
  FASTQs.
- `build-h5ad` writes the final `.h5ad` to the run's shared folder.
- `load-dataset` loads it into Bloom as a single-cell dataset with `bloomctl`, signed in as the
  pipeline. Bloom's storage keeps the file and its counts.
- `cleanup` then deletes the shared folder. Nothing from the run is written to S3.

A run started from the form is then linked to its dataset, and the dataset is given to the
scientist who asked for it.

## Starting a run by hand

Use this to run samples without the form, for example to test the pipeline. You need the Argo
CLI with the busch-lab kubeconfig (`~/.kube/kubeconfig-runai-busch-lab-argo-user.yaml`). Each
sample becomes a dataset named after the sample, under the species you give (its common name, as
in Bloom's species list).

**Into prod's Bloom:**

```bash
argo submit argo/scrna/cellranger/cellranger-count-workflow.yaml -n runai-busch-lab \
  -p samples='["col0","col1"]' -p reference=tair10_araport11 -p species=Arabidopsis --watch
```

**Into staging's Bloom** (staging's template and staging's pipeline login):

```bash
sed -e 's/name: cellranger-count-template$/name: cellranger-count-template-staging/' \
    -e 's/bloom-prod-pipeline-credentials/bloom-staging-pipeline-credentials/' \
  argo/scrna/cellranger/cellranger-count-workflow.yaml > /tmp/cellranger-count-staging.yaml
argo submit /tmp/cellranger-count-staging.yaml -n runai-busch-lab \
  -p samples='["col0","col1"]' -p reference=tair10_araport11 -p species=Arabidopsis --watch
```

- `samples` are folders under `raw_reads/` in S3, one 10x library each.
- `reference` is a folder under `reference_genome/` in S3.
- `species` is required; without it Argo refuses the submission.

A run started by hand differs from one started from the form:

- It has no run in Bloom, so it isn't on the runs page and its dataset isn't linked to a
  scientist. The dataset stays owned by the pipeline account until an admin gives it to someone.
- The form's details (accession, citation and so on) aren't attached to the dataset.
- Its step logs are still uploaded to Bloom's `run-logs` bucket, under `scrna/<workflow name>/`.

`argo get -n runai-busch-lab <workflow name>` shows its progress, and `argo logs` a step's output.
