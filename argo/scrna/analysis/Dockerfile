# The scRNA analysis steps after Cell Ranger's count: preprocess, cluster, build-h5ad, and
# load-dataset, which loads the result into Bloom with bloomctl; and download-genome, which
# fetches the run's genome version from Bloom with bloomctl before the reference is built; and
# go-pathways, the GO and pathway workflow's steps (argo/scrna/go-pathways/), which record their
# files with bloomctl.
# Build from argo/scrna/analysis/, with argo/scrna/ as the `scrna` context for the log wrapper:
#   docker build --platform linux/amd64 --build-context scrna=.. \
#     -t ghcr.io/salk-harnessing-plants-initiative/scrna-analysis:<tag> .
FROM --platform=linux/amd64 python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    NUMBA_CACHE_DIR=/tmp/numba

COPY requirements.txt /opt/scrna-analysis/requirements.txt
RUN pip install -r /opt/scrna-analysis/requirements.txt \
 && python -c "import scanpy, leidenalg, igraph, boto3" \
 && bloomctl scrna hdf5 upload --help | grep -q -- --run-id \
 && bloomctl genome download --help | grep -q -- --version-file \
 && bloomctl scrna hdf5 upload --help | grep -q -- --de-key \
 && bloomctl genome annotation download --help | grep -q -- --id-file \
 && bloomctl scrna enrichment upload --help | grep -q -- --run-id \
 && python -c "from bloomctl.scrna._cells import cluster_colours; assert len(set(cluster_colours(40))) == 40" \
 && python -c "from bloomctl.scrna import _counts; assert _counts.UPLOAD_WORKERS == 8" \
 && python -c "from bloomctl.scrna import _writer; assert len(_writer.REPEAT_DELAYS_S) == 3" \
 && python -c "from bloomctl.scrna._counts import ThreadStorages"

COPY bloom_scrna_analysis /opt/scrna-analysis/bloom_scrna_analysis
ENV PYTHONPATH=/opt/scrna-analysis
# Every step runs through it, to keep its log in Bloom's run-logs bucket (argo/scrna/run_with_log.py).
COPY --from=scrna run_with_log.py /usr/local/bin/run-with-log
COPY load-dataset.sh /usr/local/bin/load-dataset
COPY download-genome.sh /usr/local/bin/download-genome
RUN printf '#!/bin/sh\nexec python -m bloom_scrna_analysis "$@"\n' > /usr/local/bin/scrna-analysis \
 && printf '#!/bin/sh\nexec python -m bloom_scrna_analysis.bloom_dataset "$@"\n' > /usr/local/bin/bloom-dataset \
 && printf '#!/bin/sh\nexec python -m bloom_scrna_analysis.go_pathways "$@"\n' > /usr/local/bin/go-pathways \
 && chmod +x /usr/local/bin/scrna-analysis /usr/local/bin/run-with-log /usr/local/bin/load-dataset \
    /usr/local/bin/bloom-dataset /usr/local/bin/download-genome /usr/local/bin/go-pathways \
 && scrna-analysis --help >/dev/null && bloom-dataset --help >/dev/null \
 && go-pathways enrich --help >/dev/null && python -c "import scipy.stats" \
 && python -c "from bloomctl.scrna import _session; from bloomctl.credentials import DEFAULT_PROFILE" \
 && python -c "from postgrest.exceptions import APIError; from storage3.exceptions import StorageApiError; import httpx"

# Runs as root like the Cell Ranger steps, which create the run's folder on the share.
ENTRYPOINT ["scrna-analysis"]
