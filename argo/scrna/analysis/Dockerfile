# The scRNA analysis steps after Cell Ranger's count: preprocess, cluster, build-h5ad.
# Build from argo/scrna/analysis/:
#   docker build --platform linux/amd64 -t ghcr.io/salk-harnessing-plants-initiative/scrna-analysis:<tag> .
FROM --platform=linux/amd64 python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    NUMBA_CACHE_DIR=/tmp/numba

COPY requirements.txt /opt/scrna-analysis/requirements.txt
RUN pip install -r /opt/scrna-analysis/requirements.txt \
 && python -c "import scanpy, leidenalg, igraph, boto3"

COPY bloom_scrna_analysis /opt/scrna-analysis/bloom_scrna_analysis
ENV PYTHONPATH=/opt/scrna-analysis
RUN printf '#!/bin/sh\nexec python -m bloom_scrna_analysis "$@"\n' > /usr/local/bin/scrna-analysis \
 && chmod +x /usr/local/bin/scrna-analysis \
 && scrna-analysis --help >/dev/null

# Runs as root like the Cell Ranger steps, which create the run's folder on the share.
ENTRYPOINT ["scrna-analysis"]
