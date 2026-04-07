FROM python:3.14-slim@sha256:5e59aae31ff0e87511226be8e2b94d78c58f05216efda3b07dbbed938ec8583b

# Install Node.js (required for stdio-based MCP servers like web-search)
RUN apt-get update && apt-get install -y --no-install-recommends curl && \
    curl -fsSL https://deb.nodesource.com/setup_20.x | bash - && \
    apt-get install -y --no-install-recommends nodejs && \
    apt-get clean && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Extra deps for data analysis tools
RUN pip install --no-cache-dir pandas matplotlib scipy numpy scikit-learn seaborn statsmodels

COPY . .

# Create plots directory (shared with bloommcp via volume mount)
RUN mkdir -p /app/data/PLOTS_DIR

RUN addgroup --system bloom && adduser --system --ingroup bloom bloom
RUN chown -R bloom:bloom /app

EXPOSE 5002

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD curl -sf http://localhost:5002/health || exit 1

USER bloom
CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "5002", "--reload"]
