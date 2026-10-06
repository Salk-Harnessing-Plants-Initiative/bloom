# scRNA: Cell Ranger on Run:ai

Runs `cellranger count` on 10x reads stored in S3, as Argo workflows in the `runai-busch-lab` namespace.

## Files

```
argo/scrna/
├── Dockerfile                  one image for both steps: Cell Ranger, FastQC, AWS CLI, Python, run-count, run-qc
├── fastq_qc/                   run first: check the FASTQs and guess the chemistry
│   ├── fastq_qc.py             installed as fastq-qc
│   ├── qc_report.py            installed as qc-report: writes README.md for the QC folder
│   ├── run-qc.sh               installed as run-qc: downloads one sample, runs fastq-qc and FastQC; the QC-only workflow uploads the report
│   ├── fastq-qc-template.yaml  WorkflowTemplate with the fastq-qc step
│   └── fastq-qc-workflow.yaml  QC only, for a list of samples: a quick look before counting
├── analysis/                   after count: scanpy steps, in their own image
│   ├── Dockerfile              scrna-analysis image: Python, scanpy, leidenalg
│   └── bloom_scrna_analysis/   installed as scrna-analysis: preprocess, cluster, build-h5ad
└── cellranger/                 then: cellranger count
    ├── run-count.sh            installed as run-count: one sample from S3 through cellranger count and back
    ├── cellranger-count-template.yaml   WorkflowTemplate: sample-pipeline (stage → qc → count → cleanup, then preprocess → cluster → build-h5ad), stage-reference, testrun
    ├── cellranger-count-workflow.yaml   stage the reference once, then the sample pipeline for a list of samples in parallel
    └── cellranger-testrun-workflow.yaml Cell Ranger's bundled tiny dataset, to check the setup
```

## S3 layout

Bucket `bloomv2-workflows` (us-west-2):

| Prefix | Job access |
|---|---|
| `raw_reads/<sample>/` | read. FASTQs in Illumina naming, `<prefix>_S<n>_L<lane>_R1_001.fastq.gz` (barcode and UMI) and `…_R2_001.fastq.gz` (cDNA) for every lane, compressed or plain `.fastq`; `I1`/`I2` optional. The prefix is everything before `_S<n>_L<lane>_…` and can differ from the folder name |
| `reference_genome/<reference>/` | read. A `cellranger mkref` output folder |
| `runs_output/<run-id>/` | write. From the count workflow, only `h5ad/<sample>.h5ad`, `summary.json` and a `_SUCCESS` marker (written last), by build-h5ad; `qc/` from the QC-only workflow |

A run whose `runs_output/<run-id>/h5ad/_SUCCESS` already exists skips every step, and `run-count` exits straight away if the run's folder already holds its `outs/_SUCCESS`. Nothing else from the count workflow reaches S3: Cell Ranger runs without a BAM, and its web summary, raw matrix and QC report are deleted with the run's folder. Its metrics go into the final file. Cell Ranger's own run id is the sample name, so its web summary is titled with the sample; the run id goes in the summary's description. A sample name must therefore be letters, digits, `_` or `-`, at most 64 characters, or `run-count` exits 6 before downloading anything.

The FASTQs' prefix is read from their names by `fastq-sample-prefix` and passed to Cell Ranger as `--sample`, so files keep the names the sequencer or core gave them (e.g. `L007-259_S1_L002_R1_001.fastq.gz` under `raw_reads/root_rep1/`). Several prefixes in one folder are counted together as one sample. A `.fastq.gz` or `.fastq` not named that way, or a lane without both R1 and R2, fails the stage step with exit 7 and a message listing the files, before QC and count run. Setting `FASTQ_SAMPLE` overrides the detected prefix.

A run can instead read its FASTQs from any S3 folder, given as the sample pipeline's `fastq-url` (`s3://<bucket>/<folder>/`) with `fastq-files`, the JSON list of each file's name, size and ETag recorded when the run was started. The stage step (`stage-fastqs` in the image):
- lists the folder anonymously, as the start API checked it. If that's refused (a private folder shared with Bloom), it lists, copies and checks every file signed with the step's `AWS_*` key instead: `bloomv2-workflows-job`, whose policy reads another account's bucket only where its owner granted it. It then compares the listing with `fastq-files`. Only `.fastq`/`.fastq.gz` files directly in the folder count. Other files and subfolders are left alone;
- waits up to 10 minutes, checking every 30 s, while the folder holds no FASTQ or only some of the files, in case an upload is still finishing; The start API saw the whole folder when the run was started, so in practice the wait covers a folder deleted and re-uploaded in the meantime. A file re-uploaded with different contents has a new ETag and fails at once, and a deleted file only fails once the wait is over. The JSON handling (comparing the listing, checking the file list) is `stage-fastqs-lib` (`stage_fastqs_lib.py`) in the image;
- fails with exit 8, before copying anything, if a file was added, resized or replaced (its ETag differs) since the run was started, or one is still missing after the wait. It fails with exit 9, also before copying, if the recorded FASTQs are named for another sample;
- copies exactly those files onto `/shared/runs/<run-id>/fastq/<sample>/` and writes nothing to S3. Cleanup deletes them with the run's folder. Each copy is then checked: it must have the recorded size, and the object the recorded ETag (`head-object --if-match`). A file replaced while it was being copied fails with exit 8, and its copy is removed.

The run's folder is named by its run key, which a new run can reuse after a failed one. So the stage step records what the folder was built from in `/shared/runs/<run-id>/.inputs`, whatever the source. A retry on the same inputs keeps the folder and resumes. Otherwise the old FASTQs are deleted and the rest of the folder, its logs and Cell Ranger's errors, is moved to `/shared/runs/<run-id>.failed-<time>/` for debugging, so none of an earlier run's reads or results are taken for this run's. `sra/`, which fetch-sra writes earlier in the same run, stays.

A folder still empty after the wait fails with exit 4. A malformed `fastq-url` or `fastq-files` fails with exit 6, and a listing or copy that fails (a network or permission error) with exit 10, which is retried. Without `fastq-url`, the step reads `raw_reads/<sample>/` as above.

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
  -t ghcr.io/salk-harnessing-plants-initiative/cellranger:10.1.0-11 argo/scrna
docker push ghcr.io/salk-harnessing-plants-initiative/cellranger:10.1.0-11
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
# Prod and staging share runai-busch-lab, so each runs its own copy of the Cell Ranger template,
# named by WORKFLOWS_RNASEQ_CELLRANGER_TEMPLATE in .env.*.defaults: prod's is
# cellranger-count-template, staging's cellranger-count-template-staging. A new template or
# image goes to staging's copy first, and to prod's when staging is promoted:
sed 's/^  name: cellranger-count-template$/  name: cellranger-count-template-staging/' \
  argo/scrna/cellranger/cellranger-count-template.yaml | kubectl apply -n runai-busch-lab -f -
argo submit argo/scrna/fastq_qc/fastq-qc-workflow.yaml -n runai-busch-lab -p samples='["sample_a","sample_b"]' --watch
argo submit argo/scrna/cellranger/cellranger-testrun-workflow.yaml -n runai-busch-lab --watch
argo submit argo/scrna/cellranger/cellranger-count-workflow.yaml -n runai-busch-lab \
  -p samples='["sample_a","sample_b"]' -p reference=tair10_araport11 --watch
```

Steps run under `priorityClassName: high` (non-preemptible) and retry up to twice.

## QC and chemistry

The count workflow runs each sample as a chain of pods that share one NFS folder: **stage** downloads the reads into `/hpi/hpi_dev/users/bfernando/scrna/runs/<run-id>/`, **qc** checks them there, **count** runs Cell Ranger on those reads (its working files stay on the count pod's own disk, because the share does not support symlinks), and **cleanup** deletes the folder once the analysis steps have finished. The reference is downloaded once per workflow into `…/scrna/ref/<reference>/` and kept. A run whose final file is already in S3 skips them all. Within a run, the QC report stays in `/shared/runs/<run-id>/qc/<run-id>/`. The QC-only workflow runs QC alone, for a look at the reads before committing to a count, and uploads the report to `runs_output/<run-id>/qc/`. The report holds:

- `fastq_stats.tsv`: reads and min/mean/max length for every FASTQ.
- `qc_summary.json`: total read pairs, R1/R2 lengths, the FASTQ prefix to pass as `--sample`, the chemistry guess and each barcode list's score.
- `README.md`: the results in plain English, with each FastQC warning marked as expected for 10x data or worth a look.
- `run.log`: everything the QC step printed.
- `fastqc/`: a FastQC report for every R1 and R2 file. Judge quality on R2; R1 is barcode + UMI and always fails FastQC's base-content checks.

The guess checks the first 16 bases of R1 against each 10x barcode whitelist bundled with Cell Ranger (the table in `fastq_qc.py` comes from Cell Ranger's `chemistry_defs.json`) and picks the list with the most exact matches: `3M-february-2018` is 3' v3/v3.1, `3M-3pgex-may-2023` is 3' v4, `3M-5pgex-jan-2023` is 5' v3, and `737K-august-2016` is 3' v2 or 5' v1/v2 (the whitelist cannot tell those two apart). QC fails if R1 is shorter than the barcode plus UMI (28 bp for v3 and later), if a lane's R1 and R2 read counts differ, or if no barcode list matches half the reads.

Cell Ranger itself runs with `--chemistry auto`. After each count, `run-count` compares the chemistry Cell Ranger picked with the same guess and writes the result to `qc_summary.json` (`cellranger_chemistry.matches_guess`), which ends up in the final file's `uns['qc_summary']`; a mismatch is logged as a warning and does not fail the run.

## Analysis after the count

Three steps follow `count`, each its own pod, from the `scrna-analysis` image. They pass their files through the run's own shared folder, `/shared/runs/<run-id>/`, so runs going at the same time never touch each other's files, and nothing between steps is downloaded from S3. The count step leaves its matrix and metrics there too. Only the final file is uploaded:

| Step | Reads | Writes (in `/shared/runs/<run-id>/`) |
|---|---|---|
| **preprocess** | `outs/filtered_feature_bc_matrix.h5`, and Cell Ranger's `metrics_summary.csv` and `qc_summary.json` when count kept them | `analysis/preprocess/base.h5ad`: cells with ≥ 200 genes, genes in ≥ 3 cells; raw counts in `layers['counts']`, `normalize_total` (10,000) + `log1p` in `X`; the variable genes (`n-top-genes`, default 2000) and the PCA |
| **cluster** | the base's PCA | `analysis/cluster/part.h5ad`: Leiden clusters (`obs['leiden']`, resolution 1.0) and the UMAP (`obsm['X_umap']`) |
| **build-h5ad** | the base and every finished part | `h5ad/<sample>.h5ad` and `summary.json`, then the same two files and `_SUCCESS` to `runs_output/<run-id>/h5ad/` in S3 |

The final file has the barcodes as `obs_names`, gene IDs as `var_names` (Cell Ranger's names in `var['gene_symbols']`), Cell Ranger's metrics in `uns['cellranger_metrics']` (cells, reads per cell, mapping rates, as Cell Ranger wrote them), the chemistry check in `uns['qc_summary']`, the steps, settings and package versions in `uns['bloom_pipeline']`, and the run's sample name on every cell in `obs['sample']`, the column `bloomctl scrna hdf5 upload` reads each cell's sample from. It passes `bloomctl scrna hdf5 upload`'s structure check, so it loads with no extra flags.

**Adding an analysis.** Write a module that reads the base and returns `steps.part_of(base, "<name>", obs=…, obsm=…, params=…)`: a file with no matrix, holding only its own `obs` columns, `obsm` arrays and `uns['<name>']`. Then add a template and a DAG task after `preprocess`, and add the task to `build-h5ad`'s `depends`. `build-h5ad` merges parts in name order. It fails with exit 15, writing nothing, if a part's cells aren't the base's or it reuses a key that's already taken.

Each step writes its `_SUCCESS` last and does nothing if it's already there, so a retry redoes only what's missing. `build-h5ad` rebuilds when the set of finished parts has changed. `cleanup` runs after `build-h5ad`, so the folder is kept after a failure. Exit 13 means fewer than 50 cells passed the filters, and exit 14 means the count matrix is missing; neither is retried.

The image holds nothing licensed, but it's pushed private like the Cell Ranger one:

```bash
docker buildx build --platform linux/amd64 --build-context scrna=argo/scrna \
  -t ghcr.io/salk-harnessing-plants-initiative/scrna-analysis:0.1.1 argo/scrna/analysis
docker push ghcr.io/salk-harnessing-plants-initiative/scrna-analysis:0.1.1
# tests (need scanpy), from the repo root:
docker run --rm --entrypoint sh -v "$PWD:/repo" -w /repo \
  ghcr.io/salk-harnessing-plants-initiative/scrna-analysis:0.1.1 \
  -c 'pip install -q pytest && python -m pytest -q tests/unit/test_scrna_analysis.py'
```

## Step logs

Every step of the Cell Ranger template runs through `run-with-log` (`run_with_log.py`, in both images). It passes the step's output through to the pod's log as before, and keeps a copy in Bloom's `run-logs` bucket at `scrna/<workflow name>/<step>.log`, uploaded every 30 seconds while it grows (`RUN_LOG_INTERVAL` changes that) and once more when the step's command ends, whether it succeeded or failed. The run page reads it from there, so a step's log outlives its pod.

- It signs in with the `bloom-credentials` volume, mounted at `/etc/bloom/credentials.txt`: the environment's pipeline Secret, which the workflows service adds to every RNA-seq workflow. Without it (dev, or a hand-submitted run) the step runs and uploads nothing.
- An upload never fails a step; the step's exit code is the command's own.
- A retried step adds to its log rather than replacing it: the new attempt starts with what the earlier ones uploaded, under a `--- run-with-log: retried at … ---` line.
- A container killed outright gets no last upload: out of memory, or a stopped run that outlasts Kubernetes' 30-second grace. The stored log then ends up to 30 seconds early; the pod's own log has the rest until Argo deletes the workflow, 24 hours after it finishes.
- It signs in over https only, and follows no redirect; a `BLOOM_API_URL` that isn't https means no uploads.
- Logs over 45 MB keep their end. The bucket takes plain text up to 50 MB.
- The bucket grows with every run. To prune, sign in to Studio as an admin and delete old runs' folders under `run-logs/scrna/<workflow name>/`.

## Scratch storage

The count workflow's pods mount `/hpi/hpi_dev/users/bfernando/scrna` (NFS, mounted on every GPU node, `hostPath` with `type: Directory`). Staged reads and references survive retries, so a retried step never downloads them again; the run folder is deleted after success and kept after a failure. Cell Ranger's own working folder needs symlinks, which the share does not support, so it lives on the count pod's `emptyDir` and a retried count restarts Cell Ranger from the staged files. If count fails, its log and Cell Ranger's errors are kept in the run's folder, at `logs/count.log`. The share is readable by every pod on the cluster, so reads and intermediate files sit there only while a run is in progress. Each run has its own folder, so several runs can go at once. The QC-only workflow and `testrun` use an `emptyDir` deleted with the pod.
