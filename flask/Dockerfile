FROM python:3.11-slim

RUN apt-get update && apt-get install -y ffmpeg && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY config.py .
COPY app.py .
COPY videoWriter.py .
COPY static/ ./static/

EXPOSE 5002

CMD ["python", "app.py"]
