# scRNA: Cell Ranger on Run:ai

Runs `cellranger count` on 10x reads from an S3 folder or from SRA, as Argo workflows in the `runai-busch-lab` namespace.

## Files

```
argo/scrna/
├── Dockerfile                  one image for the read and count steps: Cell Ranger, FastQC, sra-tools, AWS CLI, Python, run-count, run-qc
├── fastq_qc/                   run first: check the FASTQs and guess the chemistry
│   ├── fastq_qc.py             installed as fastq-qc
│   ├── qc_report.py            installed as qc-report: writes README.md for the QC folder
│   ├── run-qc.sh               installed as run-qc: runs fastq-qc and FastQC on reads already in the run's folder
│   └── fastq-qc-workflow.yaml  QC only, for one sample: a quick look before counting, with the Cell Ranger template's steps
├── analysis/                   after count: scanpy steps, in their own image
│   ├── Dockerfile              scrna-analysis image: Python, scanpy, leidenalg
│   ├── bloom_scrna_analysis/   installed as scrna-analysis: preprocess, cluster, de, build-h5ad; and as go-pathways
│   ├── download-genome.sh      installed as download-genome: fetches the run's genome version from Bloom with bloomctl
│   └── load-dataset.sh         installed as load-dataset: loads the final .h5ad into Bloom with bloomctl
└── cellranger/                 then: cellranger count
    ├── run-count.sh            installed as run-count: cellranger count on one sample's staged reads
    ├── fetch-sra.sh            installed as fetch-sra: an SRA run's reads into the run's folder, from ENA or SRA
    ├── stage-fastqs.sh         installed as stage-fastqs: an S3 folder run's reads into the run's folder
    ├── claim-run-dir.sh        installed as claim-run-dir: makes the run's folder this run's own before either writes to it
    ├── cellranger-count-template.yaml   WorkflowTemplate: sample-pipeline (download-genome → build-reference alongside fetch-sra or stage → qc, then count → preprocess → cluster → de → build-h5ad → load-dataset → cleanup), testrun
    ├── cellranger-count-workflow.yaml   a run started by hand: the sample pipeline for a list of samples on one genome version, each loaded into Bloom
    └── cellranger-testrun-workflow.yaml Cell Ranger's bundled tiny dataset, to check the setup
go-pathways/                    GO and KEGG pathway enrichment, run by hand for now
├── go-pathways-template.yaml   WorkflowTemplate: genome-annotation and scrna-enrichment
├── genome-annotation-workflow.yaml   a genome version's annotation, from eggNOG-mapper or TAIR, recorded in Bloom
└── scrna-enrichment-workflow.yaml    a dataset's enrichment against one annotation build, recorded in Bloom
```

## S3 layout

Nothing in the pipeline writes to S3, and it reads only one thing there: an S3 folder run's FASTQs, from the scientist's own folder (below). SRA reads come from ENA or SRA, the genome from Bloom, and the result goes into Bloom. FASTQs are in Illumina naming, `<prefix>_S<n>_L<lane>_R1_001.fastq.gz` (barcode and UMI) and `…_R2_001.fastq.gz` (cDNA) for every lane, compressed or plain `.fastq`; `I1`/`I2` optional. The prefix is everything before `_S<n>_L<lane>_…` and can differ from the sample name.

`run-count` skips Cell Ranger if the run's folder already holds its `outs/_SUCCESS` (a new run on a failed run's folder), but still stores the report kept in that folder's `reports/` under the new run. Nothing from the count workflow reaches S3: Cell Ranger runs without a BAM, and its raw matrix and QC report are deleted with the run's folder. Its metrics go into the final file. Its web summary and metrics are stored in Bloom's `scrna` bucket at `reports/<run's Bloom id>/` (`web_summary.html`, `metrics_summary.csv`), or under the workflow's name for a run started by hand, through `run-with-log --put` and the pipeline's Bloom login. A failed upload is logged and doesn't fail the step; that run then has no report in Bloom, since the run's folder, `reports/` included, is deleted once the run succeeds. Cell Ranger's own run id is the sample name, so its web summary is titled with the sample; the run id goes in the summary's description. A sample name must therefore be letters, digits, `_` or `-`, at most 64 characters, or `run-count` exits 6 before downloading anything.

The FASTQs' prefix is read from their names by `fastq-sample-prefix` and passed to Cell Ranger as `--sample`, so files keep the names the sequencer or core gave them (e.g. `L007-259_S1_L002_R1_001.fastq.gz` in a folder for `root_rep1`). Several prefixes in one folder are counted together as one sample. A `.fastq.gz` or `.fastq` not named that way, or a lane without both R1 and R2, fails the stage step with exit 7 and a message listing the files, before QC and count run. Setting `FASTQ_SAMPLE` overrides the detected prefix.

A run reads its FASTQs from an S3 folder, given as the sample pipeline's `fastq-url` (`s3://<bucket>/<folder>/`) with `fastq-files`, the JSON list of each file's name, size and ETag recorded when the run was started. The stage step (`stage-fastqs` in the image):
- lists the folder anonymously, as the start API checked it. If that's refused (a private folder shared with Bloom), it lists, copies and checks every file signed with the step's `AWS_*` key instead: `bloomv2-workflows-job`, whose policy reads another account's bucket only where its owner granted it. It then compares the listing with `fastq-files`. Only `.fastq`/`.fastq.gz` files directly in the folder count. Other files and subfolders are left alone;
- waits up to 10 minutes, checking every 30 s, while the folder holds no FASTQ or only some of the files, in case an upload is still finishing; The start API saw the whole folder when the run was started, so in practice the wait covers a folder deleted and re-uploaded in the meantime. A file re-uploaded with different contents has a new ETag and fails at once, and a deleted file only fails once the wait is over. The JSON handling (comparing the listing, checking the file list) is `stage-fastqs-lib` (`stage_fastqs_lib.py`) in the image;
- fails with exit 8, before copying anything, if a file was added, resized or replaced (its ETag differs) since the run was started, or one is still missing after the wait. It fails with exit 9, also before copying, if the recorded FASTQs are named for another sample;
- copies exactly those files onto `/shared/runs/<run-id>/fastq/<sample>/` and writes nothing to S3. Cleanup deletes them with the run's folder. Each copy is then checked: it must have the recorded size, and the object the recorded ETag (`head-object --if-match`). A file replaced while it was being copied fails with exit 8, and its copy is removed.

The run's folder is named by its run key, which a new run can reuse after a failed one. So whichever step gets the reads (stage or fetch-sra) first claims the folder with `claim-run-dir`, which records what it was built from in `/shared/runs/<run-id>/.inputs`. A retry on the same inputs keeps the folder and resumes. Otherwise the old FASTQs are deleted and the rest of the folder, its logs and Cell Ranger's errors, is moved to `/shared/runs/<run-id>.failed-<time>/` for debugging, so none of an earlier run's reads or results are taken for this run's.

A folder still empty after the wait fails with exit 4. A malformed `fastq-url` or `fastq-files` fails with exit 6, and a listing or copy that fails (a network or permission error) with exit 10, which is retried. The step runs only for a folder run; without `fastq-url` it refuses with exit 6.

Exactly one of `stage` and `fetch-sra` runs, chosen by `sra-runs`: a run with SRA run IDs imports them, and any other is staged from its S3 folder, so a run given neither is refused by `stage` with exit 6.

A run can instead import its sample from SRA. The template's `fetch-sra` step (`fetch-sra` in the image) takes 1 to 9 run accessions, separated by commas, spaces or newlines, and downloads them straight into the run's folder, `/shared/runs/<run-id>/fastq/<sample>/`, as `<sample>_S1_L00<n>_<read>_001.fastq.gz`, one lane per run in the order given. Nothing is written to S3, the step holds no AWS key, and no uncompressed FASTQ is written anywhere. For each run it:
- asks ENA for the run's `.fastq.gz` files. If there are at least two, it reads the first 4,000 reads of each (without downloading the rest) to tell them apart, downloads them, and checks each against ENA's MD5;
- otherwise, or if ENA can't be reached, its files aren't one 10x library's, or a download doesn't check out, converts the run from SRA: `prefetch` downloads the `.sra` (compressed) into the run's folder, and `fastq-dump --split-spot --stdout` streams it, technical reads included (10x's barcode read is one), split by read and compressed by `pigz` as it goes. The `.sra` is deleted once the run is done;
- tells the reads apart by length: 6–12 bp is I1 then I2; of the two longer reads, a 26–28 bp one is R1 and the other R2. If both are longer than 28 bp (R1 left untrimmed, e.g. a 2×150 run), R1 is the one whose first 16 bases are on a 10x barcode list in the image, for at least half of 4,000 sampled reads.

`fastq-dump` names each record `@<acc>.<spot> <read>`; the read number picks the file, and only `@<acc>.<spot>` is written, so a spot's reads have the same name in R1, R2 and the index files, as Cell Ranger requires. Each `pigz` is checked when its file is closed, so a file it couldn't finish (a full disk) fails the step with the read it couldn't write. Each download and conversion logs its size every minute, and is stopped, failing the step, once its output hasn't grown for 30 minutes (`POLL_SECONDS`, `STALL_SECONDS`).

Each finished lane is recorded, so a retry downloads only the lanes still missing, and a `.sra-runs` marker, written last, makes a retry of a finished download do nothing. A lane that fails is removed with its `.sra`. A run without a barcode read or a cDNA read, e.g. one submitted only as a BAM, fails with exit 11. A run that can't be downloaded or converted fails with exit 10 and is retried once. Cleanup deletes the reads with the run's folder; a failed run keeps them, so starting it again resumes.

## The image

The 10x licence does not allow redistributing Cell Ranger, so the image is built by hand and pushed to a **private** GHCR package. CI never builds it. The tarball stays outside the repo and reaches the build through a named build context:

```bash
docker buildx build --platform linux/amd64 \
  --build-context cellranger=$HOME/Downloads \
  -t ghcr.io/salk-harnessing-plants-initiative/cellranger:10.1.0-14 argo/scrna
docker push ghcr.io/salk-harnessing-plants-initiative/cellranger:10.1.0-14
gh api orgs/Salk-Harnessing-Plants-Initiative/packages/container/cellranger --jq .visibility   # must print: private
```

## Cluster prerequisites

- The shared `argo-user` kubeconfig for `runai-busch-lab`, and the `argo` CLI.
- Two Run:ai credentials with **Project scope: busch-lab**, created in the Run:ai console:
  - Generic secret `bloomv2-s3` with keys `AWS_ACCESS_KEY_ID` and `AWS_SECRET_ACCESS_KEY` (becomes the Kubernetes secret `genericsecret-bloomv2-s3`). Only the stage step uses it, to read a folder a scientist shared with Bloom.
  - Docker registry credential `bloom-ghcr-pull` for `ghcr.io` with a `read:packages` token (becomes the Kubernetes secret `dockerregistry-bloom-ghcr-pull`, used in `imagePullSecrets` in the workflow files).

## Running

```bash
export KUBECONFIG=~/.kube/kubeconfig-runai-busch-lab-argo-user.yaml
argo template create argo/scrna/cellranger/cellranger-count-template.yaml -n runai-busch-lab
# after editing a template: kubectl replace -f <template file> -n runai-busch-lab
# Prod and staging share runai-busch-lab, so each runs its own copy of the Cell Ranger template,
# named by WORKFLOWS_RNASEQ_CELLRANGER_TEMPLATE in .env.*.defaults: prod's is
# cellranger-count-template, staging's cellranger-count-template-staging. A new template or
# image goes to staging's copy first, and to prod's when staging is promoted:
sed 's/^  name: cellranger-count-template$/  name: cellranger-count-template-staging/' \
  argo/scrna/cellranger/cellranger-count-template.yaml | kubectl apply -n runai-busch-lab -f -
# Apply a template before deploying the workflows service that uses it: a template without
# load-dataset's inputs ignores the dataset name, and the run loads nothing. Promoting to prod
# also needs the pipeline's database rights migrated on prod and prod's pipeline Secret
# signing in as bloom_workflows, then:
#   kubectl apply -n runai-busch-lab -f argo/scrna/cellranger/cellranger-count-template.yaml
argo submit argo/scrna/fastq_qc/fastq-qc-workflow.yaml -n runai-busch-lab -p sample=root_tip -p sra-runs=SRR28503597 --watch
argo submit argo/scrna/cellranger/cellranger-testrun-workflow.yaml -n runai-busch-lab --watch
# A Cell Ranger run by hand, loaded into Bloom (the staging variant is in _WIKI/ARGO/README.md):
argo submit argo/scrna/cellranger/cellranger-count-workflow.yaml -n runai-busch-lab \
  -p samples='[{"sample": "root_tip", "sra_runs": "SRR28503597", "fastq_url": "", "fastq_files": ""}]' \
  -p reference=athaliana_col0.v2 -p species=Arabidopsis --watch
```

Steps run under `priorityClassName: high` (non-preemptible) and retry up to twice.

## QC and chemistry

The count workflow runs each sample as a chain of pods that share one NFS folder: **fetch-sra** or **stage** puts the reads into `/hpi/hpi_dev/users/bfernando/scrna/runs/<run-id>/`, **qc** checks them there, **count** runs Cell Ranger on those reads (its working files stay on the count pod's own disk, because the share does not support symlinks), and **cleanup** deletes the folder once **load-dataset** has loaded the result into Bloom. The reference is built in every run, in the same folder (below). Within a run, the QC report stays in `/shared/runs/<run-id>/qc/<run-id>/`. The QC-only workflow runs QC alone, for a look at the reads before committing to a count: it gets one sample's reads with the same fetch-sra or stage step into `/shared/runs/qc-<workflow name>/`, and keeps the report in `qc/` there. Its exit handler deletes the reads and any `.sra` however the workflow ends, failed or not. Nothing goes to S3. The report holds:

- `fastq_stats.tsv`: reads and min/mean/max length for every FASTQ.
- `qc_summary.json`: total read pairs, R1/R2 lengths, the FASTQ prefix to pass as `--sample`, the chemistry guess and each barcode list's score.
- `README.md`: the results in plain English, with each FastQC warning marked as expected for 10x data or worth a look.
- `run.log`: everything the QC step printed.
- `fastqc/`: a FastQC report for every R1 and R2 file. Judge quality on R2; R1 is barcode + UMI and always fails FastQC's base-content checks.

The guess checks the first 16 bases of R1 against each 10x barcode whitelist bundled with Cell Ranger (the table in `fastq_qc.py` comes from Cell Ranger's `chemistry_defs.json`) and picks the list with the most exact matches: `3M-february-2018` is 3' v3/v3.1, `3M-3pgex-may-2023` is 3' v4, `3M-5pgex-jan-2023` is 5' v3, and `737K-august-2016` is 3' v2 or 5' v1/v2 (the whitelist cannot tell those two apart). QC fails if R1 is shorter than the barcode plus UMI (28 bp for v3 and later), if a lane's R1 and R2 read counts differ, or if no barcode list matches half the reads.

Cell Ranger itself runs with `--chemistry auto`. After each count, `run-count` compares the chemistry Cell Ranger picked with the same guess and writes the result to `qc_summary.json` (`cellranger_chemistry.matches_guess`), which ends up in the final file's `uns['qc_summary']`; a mismatch is logged as a warning and does not fail the run.

## The reference, from Bloom

Each run's reference is a genome version stored in Bloom (`bloomctl genome upload`), recorded on the run when it starts, and passed to the sample pipeline as `reference`, written `<genome>.v<N>`. Nothing is read from S3 and nothing is kept between runs:

- **download-genome** (`analysis/download-genome.sh`, in the `scrna-analysis` image) runs `bloomctl genome download <genome>.v<N> --unzip` into `/shared/runs/<run-id>.genome/genome/`, signed in as the pipeline like `load-dataset`. Each file is checked against the SHA-256 and size Bloom recorded, and `VERSION` records the exact version. Exit 3 (the version isn't available, or a file doesn't match) and 6 (bad input) aren't retried, nor is 16 (no credentials); exit 1 (a network or storage error) is, up to twice.
- **build-reference** (the Cell Ranger image) runs `cellranger mkref` on those files on the pod's own disk, then moves the reference into `/shared/runs/<run-id>.genome/ref/<genome>.v<N>/` in one rename, from a temporary folder named for the workflow, so a half-built reference never looks done and two runs on one run key never fill the same folder. Its cores and memory are the sample pipeline's `ref-cores`, `ref-memory` and `ref-mem-gb` (8, 32Gi and 28 by default, sized for Arabidopsis). It exits 3 without the downloaded files, 6 for a bad genome name or run id, and 17 when mkref fails, with Cell Ranger's error in its log. It's retried once, but not for 3, 6 or 17, which a retry can't fix.
- Both run alongside getting the reads (**fetch-sra** for an SRA import or **stage** for a folder, then **qc**), and **count** waits for both, so an import's genome build overlaps its download. Both write to `/shared/runs/<run-id>.genome/`, next to the run's folder rather than in it, because **fetch-sra** and **stage** clear the run's folder of an earlier run's files when it starts. **cleanup** deletes that folder with the run's. A failed run keeps both, so starting it again reuses the checked genome and the built reference: the run key includes the version, and a version never changes.

## Analysis after the count

Four analysis steps follow `count`, then `load-dataset`, each its own pod, from the `scrna-analysis` image. They pass their files through the run's own shared folder, `/shared/runs/<run-id>/`, so runs going at the same time never touch each other's files, and nothing between steps is downloaded from S3. The count step leaves its matrix and metrics there too. The final file leaves the cluster only through `load-dataset`, into Bloom:

| Step | Reads | Writes (in `/shared/runs/<run-id>/`) |
|---|---|---|
| **preprocess** | `outs/filtered_feature_bc_matrix.h5`, and Cell Ranger's `metrics_summary.csv` and `qc_summary.json` when count kept them | `analysis/preprocess/base.h5ad`: cells with ≥ 200 genes, genes in ≥ 3 cells; raw counts in `layers['counts']`, `normalize_total` (10,000) + `log1p` in `X`; the variable genes (`n-top-genes`, default 2000) and the PCA |
| **cluster** | the base's PCA | `analysis/cluster/part.h5ad`: Leiden clusters (`obs['leiden']`, resolution 1.0) and the UMAP (`obsm['X_umap']`) |
| **de** | the base's `X` and the cluster labels | `analysis/de/part.h5ad`: each Leiden cluster against the rest (`uns['de']`, scanpy's `rank_genes_groups`: Wilcoxon, Benjamini–Hochberg, every gene, `pts`). A cluster of one cell is left out; with fewer than two clusters to compare, `uns['de']` is empty and there's no DE |
| **build-h5ad** | the base and every finished part | `h5ad/<sample>.h5ad`, `summary.json` and `_SUCCESS` |

The final file has the barcodes as `obs_names`, gene IDs as `var_names` (Cell Ranger's names in `var['gene_symbols']`), Cell Ranger's metrics in `uns['cellranger_metrics']` (cells, reads per cell, mapping rates, as Cell Ranger wrote them), the chemistry check in `uns['qc_summary']`, the steps, settings and package versions in `uns['bloom_pipeline']`, and the run's sample name on every cell in `obs['sample']`, the column `bloomctl scrna hdf5 upload` reads each cell's sample from. It passes `bloomctl scrna hdf5 upload`'s structure check, so it loads with no extra flags.

**Adding an analysis.** Write a module that reads the base and returns `steps.part_of(base, "<name>", obs=…, obsm=…, params=…)`: a file with no matrix, holding only its own `obs` columns, `obsm` arrays and `uns['<name>']`. Then add a template and a DAG task after `preprocess`, and add the task to `build-h5ad`'s `depends`. `build-h5ad` merges parts in name order. It fails with exit 15, writing nothing, if a part's cells aren't the base's or it reuses a key that's already taken.

Each analysis step writes its `_SUCCESS` last and does nothing if it's already there, so a retry redoes only what's missing. `build-h5ad` rebuilds when the set of finished parts has changed. `cleanup` runs after `load-dataset`, so the folder is kept after a failure. Exit 13 means fewer than 50 cells passed the filters, and exit 14 means the count matrix is missing; neither is retried.

**load-dataset** then loads `h5ad/<sample>.h5ad` into Bloom as the run's dataset: `bloomctl scrna hdf5 upload <file> --name <dataset-name> --species <species-name> --annotation leiden --create --yes` (`analysis/load-dataset.sh`, with `bloomctl` pinned in `analysis/requirements.txt`). When the file holds the de step's result, it adds `--de-key de`, so the dataset loads with its differential expression: each cluster's comparison with the rest, every tested gene, and each cluster's statistics and top markers, which fill the expression page's cluster panel and Genes tab. The worker passes the dataset name and the species' common name from the run's form, and the run's id (`bloom-run-id`); a run naming no dataset loads nothing. For a run from Bloom, `bloom-dataset pick` (`analysis/bloom_scrna_analysis/bloom_dataset.py`) chooses the name: the one this run already loaded under, else the typed name, else the first free `<name>_v2`, `<name>_v3`, …, so a run never loads into another's dataset and a retry continues its own. bloomctl tags the dataset with the run (`--run-id`), and `bloom-dataset id` writes its id to the step's `dataset-id` output, which the status poller links the run to. A run started by hand has no run id: it loads under the name as given, untagged, and reports nothing. It signs in with `/etc/bloom/credentials.txt` (the pipeline's `bloom_workflows` login), copied into the pod's own `/tmp`, and fails with exit 16 without it. bloomctl's resume state is `/shared/runs/<run-id>/bloomctl-uploads/`, so a retry (up to 2, 6 minutes apart, past bloomctl's 330-second hold after a write it couldn't confirm) continues the load; exit 2, 6 and 16 aren't retried. It uploads one counts file per gene, so a run takes tens of minutes; the step allows 6 hours. When it succeeds, the status poller links the run to its dataset and gives the dataset to the scientist (`link_rnaseq_run_dataset`).

The image holds nothing licensed, but it's pushed private like the Cell Ranger one:

```bash
docker buildx build --platform linux/amd64 --build-context scrna=argo/scrna \
  -t ghcr.io/salk-harnessing-plants-initiative/scrna-analysis:0.1.7 argo/scrna/analysis
docker push ghcr.io/salk-harnessing-plants-initiative/scrna-analysis:0.1.7
# tests (need scanpy), from the repo root:
docker run --rm --entrypoint sh -v "$PWD:/repo" -w /repo \
  ghcr.io/salk-harnessing-plants-initiative/scrna-analysis:0.1.7 \
  -c 'pip install -q pytest && python -m pytest -q tests/unit/test_scrna_analysis.py'
```

## GO and pathways

Two workflows in `go-pathways/`, started by hand for now. Both sign in to
staging's Bloom; their headers say how to point them at prod. Register the template once with
`argo template create argo/scrna/go-pathways/go-pathways-template.yaml -n runai-busch-lab`.

```bash
# 1. A genome version's annotation: GO and the species' KEGG pathways from eggNOG-mapper, or
#    Arabidopsis' curated GO from TAIR (source=tair-go).
argo submit argo/scrna/go-pathways/genome-annotation-workflow.yaml -n runai-busch-lab \
  -p genome=sbicolor.btx623.hpiv3.v1 -p source=eggnog -p kegg-org=sbi --watch
# 2. A dataset's enrichment against that annotation build (upload-annotation's log prints its id).
argo submit argo/scrna/go-pathways/scrna-enrichment-workflow.yaml -n runai-busch-lab \
  -p dataset-id=25 -p annotation-build-id=1 --watch
```

- **genome-annotation** downloads the genome version from Bloom, builds every transcript's
  protein with gffread, and runs eggNOG-mapper (diamond, 16 cores) against its database in
  `/shared/ref/eggnog/<release>/`, downloaded there once. Each gene gets the union of its
  proteins' GO terms, with every ancestor term added (`go-basic.obo`), and the KEGG pathways
  KEGG lists for the species (`kegg-org`; left empty, GO only). **upload-annotation** records
  the file in Bloom as the version's next build of the source, in the `genome-references` bucket
  at `<genome>/v<N>/annotation/<source>-b<B>.json.gz`.
- **scrna-enrichment** fetches the annotation build's file from Bloom, checked against its
  checksum, and reads the dataset's genes and its newest complete differential expression
  analysis (or `de-run-id`). For each comparison and direction, the list
  is the genes with FDR < 0.05 and |log2FC| > 0.25, strongest first, up to 300. Each of GO's
  three namespaces and KEGG is tested against its own background, the dataset's genes with a
  term of that kind: a hypergeometric test for every term of 5–500 background genes with at
  least one list gene, terms covering the same genes merged into the most specific, and
  Benjamini–Hochberg over all of them within a comparison, direction and kind. Then terms with
  FDR < 0.05 and at least 3 list genes are kept; the 3-gene rule comes after the correction,
  since dropping terms by their list-gene count before it would make the FDRs too low. The
  settings are workflow parameters. **upload-enrichment** records the file in Bloom
  as an enrichment run, in the `scrna` bucket at `enrichment/<dataset>/run-<id>.json.gz`; a
  dataset not yet linked to a genome version is first linked to the build's.
- Recording starts the build or run, uploads its file and finishes it, which checks the stored
  size; a failed upload abandons it. Neither upload step is retried, since a retry would find
  its own unfinished record. Both runs delete their folders on `/shared` once the result is in
  Bloom; a failed run keeps them.
- Exit codes: 16 without the pipeline's Bloom credential, or when Bloom won't sign in with it;
  18 for an annotation input that isn't what the step expects (eggNOG output without its
  header, a protein that isn't in the GTF, a bad KEGG organism, no gene with a term, a genome
  version that isn't ready) or a build Bloom refuses to start, store or finish; 19 for an
  enrichment input (no complete analysis, no such ready build or a file that doesn't match it,
  genes outside the dataset, a dataset sharing no gene with the annotation or linked to another
  genome version) or a link or run Bloom refuses. None is retried. A network error is exit 1,
  which the fetch steps retry.

The files (`bloom_scrna_analysis/annotation.py` and `enrichment.py`):

- `bloom-annotation/1`: `genome`, `version`, `source`, `tools` (eggNOG-mapper and database
  versions, the GO release, TAIR's file date), `terms` by ID (`kind` GO_BP, GO_MF, GO_CC or
  KEGG, `name`, and for GO the `parents` that are in the file), and `genes` by ID (`name`,
  `description`, `terms` with ancestors included, and `kos`, the gene's KEGG orthologs).
- `bloom-enrichment/1`: `dataset_id`, `de_run_id`, `annotation`, `params`, `background_genes`
  (the dataset's genes), `annotated_genes` (those with any term), and `results`: per
  significant term its comparison, direction, kind, term, name, list and term counts,
  background size, p, FDR and list genes; a KEGG term also has `kos`, its list genes' KEGG
  orthologs, which colour them on the pathway's map.

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
