# scRNA: Cell Ranger on Run:ai

Runs `cellranger count` on 10x reads stored in S3, as Argo workflows in the `runai-busch-lab` namespace.

## Files

```
argo/scrna/
├── Dockerfile                  one image for both steps: Cell Ranger, FastQC, AWS CLI, Python, run-count, run-qc
├── fastq_qc/                   run first: check the FASTQs and guess the chemistry
│   ├── fastq_qc.py             installed as fastq-qc
│   ├── qc_report.py            installed as qc-report: writes README.md for the QC folder
│   ├── run-qc.sh               installed as run-qc: downloads one sample, runs fastq-qc and FastQC, uploads the report
│   ├── fastq-qc-template.yaml  WorkflowTemplate with the fastq-qc step
│   └── fastq-qc-workflow.yaml  QC for a list of samples in parallel
└── cellranger/                 then: cellranger count
    ├── run-count.sh            installed as run-count: one sample from S3 through cellranger count and back
    ├── cellranger-count-template.yaml   WorkflowTemplate with the count and testrun steps
    ├── cellranger-count-workflow.yaml   count for a list of samples in parallel
    └── cellranger-testrun-workflow.yaml Cell Ranger's bundled tiny dataset, to check the setup
```

## S3 layout

Bucket `bloomv2-workflows` (us-west-2):

| Prefix | Job access |
|---|---|
| `raw_reads/<sample>/` | read. FASTQs in Illumina naming (`<sample>_S1_L001_R1_001.fastq.gz`) |
| `reference_genome/<reference>/` | read. A `cellranger mkref` output folder |
| `runs_output/<run-id>/` | write. `qc/` from the QC workflow; `outs/` and a `_SUCCESS` marker (written last) from the count workflow |

`run-count` exits straight away if `runs_output/<run-id>/_SUCCESS` already exists.

## The image

The 10x licence does not allow redistributing Cell Ranger, so the image is built by hand and pushed to a **private** GHCR package. CI never builds it. The tarball stays outside the repo and reaches the build through a named build context:

```bash
docker buildx build --platform linux/amd64 \
  --build-context cellranger=$HOME/Downloads \
  -t ghcr.io/salk-harnessing-plants-initiative/cellranger:10.1.0-1 argo/scrna
docker push ghcr.io/salk-harnessing-plants-initiative/cellranger:10.1.0-1
gh api orgs/Salk-Harnessing-Plants-Initiative/packages/container/cellranger --jq .visibility   # must print: private
```

## Cluster prerequisites

- The shared `argo-user` kubeconfig for `runai-busch-lab`, and the `argo` CLI.
- Two Run:ai credentials with **Project scope: busch-lab**, created in the Run:ai console:
  - Generic secret `bloomv2-s3` with keys `AWS_ACCESS_KEY_ID` and `AWS_SECRET_ACCESS_KEY` (becomes the Kubernetes secret `genericsecret-bloomv2-s3`).
  - Docker registry credential `bloom-ghcr-pull` for `ghcr.io` with a `read:packages` token (becomes the Kubernetes secret `dockerregistry-bloom-ghcr-pull`, used in `imagePullSecrets` in the workflow files).

## Running

```bash
export KUBECONFIG=~/.kube/kubeconfig-runai-busch-lab-argo-user.yaml
argo template create argo/scrna/cellranger/cellranger-count-template.yaml -n runai-busch-lab
argo template create argo/scrna/fastq_qc/fastq-qc-template.yaml -n runai-busch-lab
argo submit argo/scrna/fastq_qc/fastq-qc-workflow.yaml -n runai-busch-lab -p samples='["sample_a","sample_b"]' --watch
argo submit argo/scrna/cellranger/cellranger-testrun-workflow.yaml -n runai-busch-lab --watch
argo submit argo/scrna/cellranger/cellranger-count-workflow.yaml -n runai-busch-lab \
  -p samples='["sample_a","sample_b"]' -p reference=tair10_araport11 --watch
```

Steps run under `priorityClassName: high` (non-preemptible) and retry up to twice.

## QC and chemistry

Run the QC workflow before counting. `runs_output/<sample>/qc/` then holds:

- `fastq_stats.tsv`: reads and min/mean/max length for every FASTQ.
- `qc_summary.json`: total read pairs, R1/R2 lengths, the FASTQ prefix to pass as `--sample`, the chemistry guess and each barcode list's score.
- `README.md`: the results in plain English, with each FastQC warning marked as expected for 10x data or worth a look.
- `run.log`: everything the QC step printed.
- `fastqc/`: a FastQC report for every R1 and R2 file. Judge quality on R2; R1 is barcode + UMI and always fails FastQC's base-content checks.

The guess checks the first 16 bases of R1 against each 10x barcode whitelist bundled with Cell Ranger (the table in `fastq_qc.py` comes from Cell Ranger's `chemistry_defs.json`) and picks the list with the most exact matches: `3M-february-2018` is 3' v3/v3.1, `3M-3pgex-may-2023` is 3' v4, `3M-5pgex-jan-2023` is 5' v3, and `737K-august-2016` is 3' v2 or 5' v1/v2 (the whitelist cannot tell those two apart). QC fails if R1 is shorter than the barcode plus UMI (28 bp for v3 and later), if a lane's R1 and R2 read counts differ, or if no barcode list matches half the reads.

Cell Ranger itself runs with `--chemistry auto`. After each count, `run-count` compares the chemistry Cell Ranger picked with the same guess and writes the result to `outs/qc/qc_summary.json` (`cellranger_chemistry.matches_guess`); a mismatch is logged as a warning and does not fail the run.

## Scratch storage

`/work` is an `emptyDir`, deleted when the pod ends. A retry or rerun downloads the reads and reference from S3 again and starts Cell Ranger from the beginning; finished samples are still skipped through `_SUCCESS`. Pointing `/work` at a ReadWriteMany PVC in the project keeps downloads between runs and lets Cell Ranger resume; `run-count` needs no change for that.
