# syntax=docker/dockerfile:1
# Cell Ranger, FastQC, sra-tools and the AWS CLI for Run:ai jobs.
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

# SRA downloads for runs that import their sample: NCBI's own release (Debian's is years old), and pigz.
ARG SRATOOLKIT_VERSION=3.4.1
ARG SRATOOLKIT_SHA256=b950362c054765a4184af41947f022f040e94e964862017c0ecb0b0273db3596
RUN apt-get update \
 && apt-get install -y --no-install-recommends pigz \
 && rm -rf /var/lib/apt/lists/* \
 && curl -fsSL https://ftp-trace.ncbi.nlm.nih.gov/sra/sdk/${SRATOOLKIT_VERSION}/sratoolkit.${SRATOOLKIT_VERSION}-ubuntu64.tar.gz -o /tmp/sra.tgz \
 && echo "${SRATOOLKIT_SHA256}  /tmp/sra.tgz" | sha256sum -c - \
 && tar -xzf /tmp/sra.tgz -C /opt \
 && rm /tmp/sra.tgz \
 && for tool in prefetch fasterq-dump vdb-config; do ln -s /opt/sratoolkit.${SRATOOLKIT_VERSION}-ubuntu64/bin/${tool} /usr/local/bin/${tool}; done \
 && vdb-config --restore-defaults >/dev/null \
 && fasterq-dump --version

COPY cellranger/run-count.sh /usr/local/bin/run-count
COPY cellranger/fastq-sample-prefix.sh /usr/local/bin/fastq-sample-prefix
COPY cellranger/fetch-sra.sh /usr/local/bin/fetch-sra
COPY cellranger/stage-fastqs.sh /usr/local/bin/stage-fastqs
COPY cellranger/stage_fastqs_lib.py /usr/local/bin/stage-fastqs-lib
# Every step runs through it, to keep its log in Bloom's run-logs bucket.
COPY run_with_log.py /usr/local/bin/run-with-log
COPY fastq_qc/run-qc.sh /usr/local/bin/run-qc
COPY fastq_qc/fastq_qc.py /usr/local/bin/fastq-qc
COPY fastq_qc/qc_report.py /usr/local/bin/qc-report
RUN chmod +x /usr/local/bin/run-count /usr/local/bin/fastq-sample-prefix /usr/local/bin/fetch-sra /usr/local/bin/stage-fastqs /usr/local/bin/stage-fastqs-lib /usr/local/bin/run-with-log /usr/local/bin/run-qc /usr/local/bin/fastq-qc /usr/local/bin/qc-report

WORKDIR /work
