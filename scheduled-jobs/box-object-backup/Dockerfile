# Base image digest-pinned to match the sibling services (bloomcli, bloommcp, workflows)
FROM python:3.11-slim@sha256:9358444059ed78e2975ada2c189f1c1a3144a5dab6f35bff8c981afb38946634

# psql reads storage.objects; Debian 13's client (17) talks to the older server (15)
RUN apt-get update \
    && apt-get install -y --no-install-recommends postgresql-client \
    && rm -rf /var/lib/apt/lists/*

# rclone, pinned by digest
COPY --from=rclone/rclone:1.75.1@sha256:45401ad7410db1d67ffdb58e19059ad20b0d8e0285a60e38bbec55cc1019c7a5 /usr/local/bin/rclone /usr/local/bin/rclone

# A missing or non-static binary fails the build, not the night
RUN rclone version && psql --version

# The container's root filesystem is read-only
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1

RUN addgroup --system bloom && adduser --system --ingroup bloom bloom

WORKDIR /app
COPY *.py ./
USER bloom

ENTRYPOINT ["python3", "/app/backup_objects.py"]
