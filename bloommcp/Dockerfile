FROM python:3.11-slim@sha256:9358444059ed78e2975ada2c189f1c1a3144a5dab6f35bff8c981afb38946634

WORKDIR /app

# System deps for matplotlib rendering and scipy
RUN apt-get update && apt-get install -y --no-install-recommends \
    libfreetype6-dev \
    libpng-dev \
    libjpeg-dev \
    pkg-config \
    gcc \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Create data directories (owned by bloommcp)
RUN mkdir -p /app/data/SLEAP_OUT_CSV /app/data/PLOTS_DIR /app/data/ANALYSIS_OUTPUT

RUN addgroup --system bloom && adduser --system --ingroup bloom bloom
RUN chown -R bloom:bloom /app

EXPOSE 8811

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8811/mcp')" || exit 1

USER bloom
CMD ["python", "server.py"]
