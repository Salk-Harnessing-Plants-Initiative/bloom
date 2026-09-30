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
│   └── fastq-qc-workflow.yaml  QC only, for a list of samples: a quick look before counting
└── cellranger/                 then: cellranger count
    ├── run-count.sh            installed as run-count: one sample from S3 through cellranger count and back
    ├── cellranger-count-template.yaml   WorkflowTemplate: sample-pipeline (stage → qc → count → cleanup), stage-reference, testrun
    ├── cellranger-count-workflow.yaml   stage the reference once, then the sample pipeline for a list of samples in parallel
    └── cellranger-testrun-workflow.yaml Cell Ranger's bundled tiny dataset, to check the setup
```

## S3 layout

Bucket `bloomv2-workflows` (us-west-2):

| Prefix | Job access |
|---|---|
| `raw_reads/<sample>/` | read. FASTQs in Illumina naming, `<prefix>_S<n>_L<lane>_R1_001.fastq.gz` (barcode and UMI) and `…_R2_001.fastq.gz` (cDNA) for every lane, compressed or plain `.fastq`; `I1`/`I2` optional. The prefix is everything before `_S<n>_L<lane>_…` and can differ from the folder name |
| `reference_genome/<reference>/` | read. A `cellranger mkref` output folder |
| `runs_output/<run-id>/` | write. `qc/` from the QC workflow; `outs/` and a `_SUCCESS` marker (written last) from the count workflow |

`run-count` exits straight away if `runs_output/<run-id>/_SUCCESS` already exists. Cell Ranger's own run id is the sample name, so its web summary is titled with the sample; the run id goes in the summary's description. A sample name must therefore be letters, digits, `_` or `-`, at most 64 characters, or `run-count` exits 6 before downloading anything.

The FASTQs' prefix is read from their names by `fastq-sample-prefix` and passed to Cell Ranger as `--sample`, so files keep the names the sequencer or core gave them (e.g. `L007-259_S1_L002_R1_001.fastq.gz` under `raw_reads/root_rep1/`). Several prefixes in one folder are counted together as one sample. A `.fastq.gz` or `.fastq` not named that way, or a lane without both R1 and R2, fails the stage step with exit 7 and a message listing the files, before QC and count run. Setting `FASTQ_SAMPLE` overrides the detected prefix.

A run can also import its sample from SRA. The template's `fetch-sra` step (`fetch-sra` in the image) takes 1 to 9 run accessions, separated by commas, spaces or newlines, and:
- downloads each run accession with `prefetch` and `fasterq-dump --split-files --include-technical`, so 10x's barcode read, which SRA stores as a technical read, is kept;
- tells the reads apart by length: 6–12 bp is I1 then I2; of the two longer reads, a 26–28 bp one is R1 and the other R2. If both are longer than 28 bp (R1 left untrimmed, e.g. a 2×150 run), R1 is the one whose first 16 bases are on a 10x barcode list in the image, for at least half of 4,000 sampled reads;
- writes them to `raw_reads/<sample>/` as `<sample>_S1_L00<n>_<read>_001.fastq.gz`, one lane per run in the order given, and writes a `.sra-runs` marker last;
- outputs the folder's FASTQ count and total bytes.

A folder whose marker lists the same runs is left as it is, so a retry doesn't download again. A folder holding other FASTQs is refused (exit 12). A run without a barcode read or a cDNA read, e.g. one submitted only as a BAM, fails with exit 11. A run that can't be downloaded, or an S3 folder that can't be checked (a network or permission error), fails with exit 10 and is retried once; nothing is uploaded, so the run can simply be started again. Uncompressed FASTQs are about 7 times the size of the compressed ones, so one 12 GB run needs about 90 GB of scratch space under `/shared/runs/<run-id>/sra/` while it converts.

## The image

The 10x licence does not allow redistributing Cell Ranger, so the image is built by hand and pushed to a **private** GHCR package. CI never builds it. The tarball stays outside the repo and reaches the build through a named build context:

```bash
docker buildx build --platform linux/amd64 \
  --build-context cellranger=$HOME/Downloads \
  -t ghcr.io/salk-harnessing-plants-initiative/cellranger:10.1.0-6 argo/scrna
docker push ghcr.io/salk-harnessing-plants-initiative/cellranger:10.1.0-6
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
# after editing a template: kubectl replace -f <template file> -n runai-busch-lab
argo submit argo/scrna/fastq_qc/fastq-qc-workflow.yaml -n runai-busch-lab -p samples='["sample_a","sample_b"]' --watch
argo submit argo/scrna/cellranger/cellranger-testrun-workflow.yaml -n runai-busch-lab --watch
argo submit argo/scrna/cellranger/cellranger-count-workflow.yaml -n runai-busch-lab \
  -p samples='["sample_a","sample_b"]' -p reference=tair10_araport11 --watch
```

Steps run under `priorityClassName: high` (non-preemptible) and retry up to twice.

## QC and chemistry

The count workflow runs each sample as four pods that share one NFS folder: **stage** downloads the reads into `/hpi/hpi_dev/users/bfernando/scrna/runs/<run-id>/`, **qc** checks them there, **count** runs Cell Ranger on those reads (its working files stay on the count pod's own disk, because the share does not support symlinks), and **cleanup** deletes the folder once `_SUCCESS` is written. The reference is downloaded once per workflow into `…/scrna/ref/<reference>/` and kept. A sample that already has `_SUCCESS` skips all four. The QC workflow runs QC alone, for a look at the reads before committing to a count. Either way, `runs_output/<sample>/qc/` holds:

- `fastq_stats.tsv`: reads and min/mean/max length for every FASTQ.
- `qc_summary.json`: total read pairs, R1/R2 lengths, the FASTQ prefix to pass as `--sample`, the chemistry guess and each barcode list's score.
- `README.md`: the results in plain English, with each FastQC warning marked as expected for 10x data or worth a look.
- `run.log`: everything the QC step printed.
- `fastqc/`: a FastQC report for every R1 and R2 file. Judge quality on R2; R1 is barcode + UMI and always fails FastQC's base-content checks.

The guess checks the first 16 bases of R1 against each 10x barcode whitelist bundled with Cell Ranger (the table in `fastq_qc.py` comes from Cell Ranger's `chemistry_defs.json`) and picks the list with the most exact matches: `3M-february-2018` is 3' v3/v3.1, `3M-3pgex-may-2023` is 3' v4, `3M-5pgex-jan-2023` is 5' v3, and `737K-august-2016` is 3' v2 or 5' v1/v2 (the whitelist cannot tell those two apart). QC fails if R1 is shorter than the barcode plus UMI (28 bp for v3 and later), if a lane's R1 and R2 read counts differ, or if no barcode list matches half the reads.

Cell Ranger itself runs with `--chemistry auto`. After each count, `run-count` compares the chemistry Cell Ranger picked with the same guess and writes the result to `outs/qc/qc_summary.json` (`cellranger_chemistry.matches_guess`); a mismatch is logged as a warning and does not fail the run.

## Scratch storage

The count workflow's pods mount `/hpi/hpi_dev/users/bfernando/scrna` (NFS, mounted on every GPU node, `hostPath` with `type: Directory`). Staged reads and references survive retries, so a retried step never downloads them again; the run folder is deleted after success and kept after a failure. Cell Ranger's own working folder needs symlinks, which the share does not support, so it lives on the count pod's `emptyDir` and a retried count restarts Cell Ranger from the staged files. If count fails, its log and Cell Ranger's errors are uploaded to `runs_output/<run-id>/logs/count.log`. The share is readable by every pod on the cluster, so reads sit there only while a run is in progress. The QC-only workflow and `testrun` use an `emptyDir` deleted with the pod.
