# Base image digest-pinned to match sibling services (langchain, bloommcp)
FROM python:3.11-slim@sha256:9358444059ed78e2975ada2c189f1c1a3144a5dab6f35bff8c981afb38946634

WORKDIR /app

# ffmpeg is required by video_writer.py (H.264 encoding of scan frames)
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

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
