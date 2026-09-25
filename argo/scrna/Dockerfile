# syntax=docker/dockerfile:1
# Cell Ranger + AWS CLI for Run:ai jobs. 
# Private image only: the 10x licence forbids redistribution.
# Build: docker buildx build --platform linux/amd64 --build-context cellranger=<dir with the tarball> -t <tag> argo/scrna
# already on the repos GHRC
FROM --platform=linux/amd64 debian:bookworm-slim

ARG CELLRANGER_VERSION=10.1.0
ENV CELLRANGER_HOME=/opt/cellranger-${CELLRANGER_VERSION}

RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates curl unzip procps python3 perl openjdk-17-jre-headless \
 && rm -rf /var/lib/apt/lists/*

ARG FASTQC_VERSION=0.12.1
ARG FASTQC_SHA256=5f4dba8780231a25a6b8e11ab2c238601920c9704caa5458d9de559575d58aa7
RUN curl -fsSL https://www.bioinformatics.babraham.ac.uk/projects/fastqc/fastqc_v${FASTQC_VERSION}.zip -o /tmp/fastqc.zip \
 && echo "${FASTQC_SHA256}  /tmp/fastqc.zip" | sha256sum -c - \
 && unzip -q /tmp/fastqc.zip -d /opt \
 && chmod +x /opt/FastQC/fastqc \
 && ln -s /opt/FastQC/fastqc /usr/local/bin/fastqc \
 && rm /tmp/fastqc.zip \
 && fastqc --version

RUN curl -fsSL https://awscli.amazonaws.com/awscli-exe-linux-x86_64.zip -o /tmp/awscli.zip \
 && unzip -q /tmp/awscli.zip -d /tmp \
 && /tmp/aws/install \
 && rm -rf /tmp/aws /tmp/awscli.zip

# Bind-mount the tarball from the named build context so it never lands in an image layer.
RUN --mount=type=bind,from=cellranger,source=cellranger-${CELLRANGER_VERSION}.tar,target=/tmp/cellranger.tar \
    tar -xf /tmp/cellranger.tar -C /opt \
 && ln -s /opt/cellranger-${CELLRANGER_VERSION}/cellranger /usr/local/bin/cellranger \
 && cellranger --version

COPY cellranger/run-count.sh /usr/local/bin/run-count
COPY fastq_qc/run-qc.sh /usr/local/bin/run-qc
COPY fastq_qc/fastq_qc.py /usr/local/bin/fastq-qc
COPY fastq_qc/qc_report.py /usr/local/bin/qc-report
RUN chmod +x /usr/local/bin/run-count /usr/local/bin/run-qc /usr/local/bin/fastq-qc /usr/local/bin/qc-report

WORKDIR /work
