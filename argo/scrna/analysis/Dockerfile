# The scRNA analysis steps after Cell Ranger's count: preprocess, cluster, build-h5ad, and
# load-dataset, which loads the result into Bloom with bloomctl.
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
 && bloomctl scrna hdf5 upload --help >/dev/null

COPY bloom_scrna_analysis /opt/scrna-analysis/bloom_scrna_analysis
ENV PYTHONPATH=/opt/scrna-analysis
# Every step runs through it, to keep its log in Bloom's run-logs bucket (argo/scrna/run_with_log.py).
COPY --from=scrna run_with_log.py /usr/local/bin/run-with-log
COPY load-dataset.sh /usr/local/bin/load-dataset
RUN printf '#!/bin/sh\nexec python -m bloom_scrna_analysis "$@"\n' > /usr/local/bin/scrna-analysis \
 && printf '#!/bin/sh\nexec python -m bloom_scrna_analysis.fetch "$@"\n' > /usr/local/bin/fetch-published \
 && chmod +x /usr/local/bin/scrna-analysis /usr/local/bin/run-with-log /usr/local/bin/load-dataset /usr/local/bin/fetch-published \
 && scrna-analysis --help >/dev/null

# Runs as root like the Cell Ranger steps, which create the run's folder on the share.
ENTRYPOINT ["scrna-analysis"]
