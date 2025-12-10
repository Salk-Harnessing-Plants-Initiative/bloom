FROM python:3.11-slim

RUN apt-get update && apt-get install -y ffmpeg && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY config.py .
COPY main.py .
COPY videoWriter.py .
COPY static/ ./static/

EXPOSE 5002

# Run with uvicorn for ASGI and automatic OpenAPI docs
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "5002", "--reload"]
