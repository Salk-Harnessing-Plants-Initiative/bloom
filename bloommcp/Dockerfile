FROM python:3.11-slim

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

EXPOSE 8811

CMD ["python", "server.py"]
