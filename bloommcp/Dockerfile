FROM python:3.14-slim@sha256:5e59aae31ff0e87511226be8e2b94d78c58f05216efda3b07dbbed938ec8583b

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
