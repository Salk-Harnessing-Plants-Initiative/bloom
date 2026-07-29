# Static ffmpeg (libx264), digest-pinned multi-arch build. Copying the static
# binary avoids apt's ffmpeg, which drags the Mesa/glib/mbedtls stack and its
# unreachable CRITICAL CVEs into the image. video_writer.py only needs headless
# libx264 encoding from stdin.
FROM mwader/static-ffmpeg:8.1.2@sha256:33f770f812cbfc3de96c547157fc9faf8bd95a36481753439ffa761045167585 AS ffmpeg

# Base image digest-pinned to match sibling services (langchain, bloommcp)
FROM python:3.11-slim@sha256:9358444059ed78e2975ada2c189f1c1a3144a5dab6f35bff8c981afb38946634

WORKDIR /app

# ffmpeg is required by video_writer.py (H.264 encoding of scan frames)
COPY --from=ffmpeg /ffmpeg /usr/local/bin/ffmpeg

RUN pip install --no-cache-dir uv

COPY pyproject.toml ./
RUN uv pip install --system --no-cache .

COPY . .

# Run as a non-root system user, matching langchain-agent and bloommcp
RUN addgroup --system bloom && adduser --system --ingroup bloom bloom \
    && chown -R bloom:bloom /app
USER bloom

EXPOSE 5100

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "5100"]
