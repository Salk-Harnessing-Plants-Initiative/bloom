"""
Bloom Workflows API

HTTP endpoints for Bloom workflow tasks.

Run:
    uvicorn main:app --host 0.0.0.0 --port 5100 --reload

Endpoints:
    GET  /health                   - health check
    GET  /                         - basic test route
    POST /experiments/{id}/video   - generate the experiment's scan video, write
                                     it to S3, return a presigned download URL
"""

import os
import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from video import generate_experiment_video

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

# Comma-separated browser origins allowed to call this API (the frontend).
# CORS only restricts browser JS — it is not access control for curl/servers.
CORS_ORIGINS = os.environ.get("WORKFLOWS_CORS_ORIGINS", "http://localhost:3000").split(
    ","
)

app = FastAPI(title="Bloom Workflows API", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


class VideoRequest(BaseModel):
    # Optionally target a specific scan; otherwise the experiment's first scan.
    scan_id: int | None = Field(default=None, gt=0)


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/")
def root():
    return {"message": "Bloom Workflows API is running"}


@app.post("/experiments/{experiment_id}/video")
def experiment_video(experiment_id: int, req: VideoRequest | None = None):
    """Generate the experiment's scan video, upload to S3, return the download URL."""
    scan_id = req.scan_id if req else None
    result = generate_experiment_video(experiment_id, scan_id)
    logger.info(
        "Generated video for experiment %s scan %s (%d frames)",
        experiment_id,
        result["scan_id"],
        result["frames"],
    )
    return {"experiment_id": experiment_id, **result}
