#!/usr/bin/env python3
"""
Video Generation Listener Service

Listens for PostgreSQL notifications and processes video generation jobs.
Connects directly to PostgreSQL and uses pg_notify for real-time job processing.

Usage:
    python video_listener.py

Environment Variables:
    DATABASE_URL: PostgreSQL connection string
    S3_ENDPOINT: MinIO/S3 endpoint URL
    S3_BUCKET_NAME: S3 bucket name
    AWS_ACCESS_KEY_ID: S3 access key
    AWS_SECRET_ACCESS_KEY: S3 secret key
"""

import os
import sys
import json
import time
import select
import tempfile
import io
import logging
from datetime import datetime

import psycopg2
import psycopg2.extensions
import boto3
import numpy as np
from PIL import Image

from video_writer import VideoWriter

# logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)

# Configuration from environment
DATABASE_URL = os.environ.get('DATABASE_URL', 'postgresql://postgres:postgres@localhost:5432/postgres')
S3_ENDPOINT = os.environ.get('S3_ENDPOINT', 'http://localhost:9100')
S3_BUCKET_NAME = os.environ.get('S3_BUCKET_NAME', 'bloom-storage')
AWS_ACCESS_KEY_ID = os.environ.get('AWS_ACCESS_KEY_ID', 'supabase')
AWS_SECRET_ACCESS_KEY = os.environ.get('AWS_SECRET_ACCESS_KEY', 'supabase123')
AWS_REGION = os.environ.get('AWS_REGION', 'us-east-1')

# Video processing settings
DECIMATE_FACTOR = 4 


def get_db_connection():
    """Create a new database connection."""
    conn = psycopg2.connect(DATABASE_URL)
    conn.set_isolation_level(psycopg2.extensions.ISOLATION_LEVEL_AUTOCOMMIT)
    return conn


def get_s3_client():
    """Create S3/MinIO client."""
    return boto3.client(
        's3',
        endpoint_url=S3_ENDPOINT,
        aws_access_key_id=AWS_ACCESS_KEY_ID,
        aws_secret_access_key=AWS_SECRET_ACCESS_KEY,
        region_name=AWS_REGION
    )


def update_job_status(conn, job_id: int, status: str, **kwargs):
    """Update job status in database."""
    cur = conn.cursor()

    set_clauses = ["status = %s"]
    values = [status]

    if 'progress' in kwargs:
        set_clauses.append("progress = %s")
        values.append(kwargs['progress'])

    if 'total_frames' in kwargs:
        set_clauses.append("total_frames = %s")
        values.append(kwargs['total_frames'])

    if 'error_message' in kwargs:
        set_clauses.append("error_message = %s")
        values.append(kwargs['error_message'])

    if 'download_url' in kwargs:
        set_clauses.append("download_url = %s")
        values.append(kwargs['download_url'])

    if status == 'processing':
        set_clauses.append("started_at = %s")
        values.append(datetime.now())

    if status in ('complete', 'failed'):
        set_clauses.append("completed_at = %s")
        values.append(datetime.now())

    values.append(job_id)

    query = f"UPDATE video_jobs SET {', '.join(set_clauses)} WHERE id = %s"
    cur.execute(query, values)
    cur.close()


def get_scan_images(conn, scan_id: int):
    """Fetch scan and its images from database."""
    cur = conn.cursor()
    cur.execute("""
        SELECT ci.id, ci.object_path, ci.frame_number
        FROM cyl_images ci
        JOIN cyl_scans cs ON ci.scan_id = cs.id
        WHERE cs.id = %s
        ORDER BY ci.frame_number
    """, (scan_id,))

    images = cur.fetchall()
    cur.close()
    return images


def generate_video(conn, s3, job_id: int, scan_id: int):
    """Generate video from scan images."""
    logger.info(f"Starting video generation for job {job_id}, scan {scan_id}")

    # Update status to processing
    update_job_status(conn, job_id, 'processing')

    try:
        # Get images for this scan
        images = get_scan_images(conn, scan_id)

        if not images:
            raise Exception(f"No images found for scan {scan_id}")

        total_frames = len(images)
        update_job_status(conn, job_id, 'processing', total_frames=total_frames)
        logger.info(f"Found {total_frames} frames to process")

        with tempfile.TemporaryDirectory() as tmp_dir:
            video_path = os.path.join(tmp_dir, f"{scan_id}.mp4")
            video_writer = VideoWriter(filename=video_path)

            frames_added = 0
            images_path = "storage-single-tenant/images"

            for idx, (image_id, object_path, frame_number) in enumerate(images):
                try:
                    # Find the image in S3
                    object_prefix = f"{images_path}/{object_path}"
                    response = s3.list_objects_v2(Bucket=S3_BUCKET_NAME, Prefix=object_prefix)

                    if "Contents" not in response or len(response["Contents"]) != 1:
                        logger.warning(f"Image not found: {object_prefix}")
                        continue

                    # Download and process image
                    object_key = response["Contents"][0]["Key"]
                    obj = s3.get_object(Bucket=S3_BUCKET_NAME, Key=object_key)
                    image_bytes = obj['Body'].read()

                    # Convert to numpy array and decimate
                    image_array = np.array(Image.open(io.BytesIO(image_bytes)))
                    image_array = image_array[::DECIMATE_FACTOR, ::DECIMATE_FACTOR]

                    if image_array.size == 0:
                        continue

                    video_writer.add(image_array)
                    frames_added += 1

                    # Update progress every 10 frames
                    if frames_added % 10 == 0:
                        progress = int((frames_added / total_frames) * 100)
                        update_job_status(conn, job_id, 'processing', progress=progress)
                        logger.info(f"Progress: {progress}% ({frames_added}/{total_frames})")

                except Exception as e:
                    logger.warning(f"Error processing frame {frame_number}: {e}")
                    continue

            video_writer.close()

            if not os.path.exists(video_path):
                raise Exception("Video file was not created")

            if frames_added == 0:
                raise Exception("No frames were added to the video")

            # Upload video to S3
            s3_key = f"cyl-videos/{scan_id}.mp4"
            s3.upload_file(
                Filename=video_path,
                Bucket=S3_BUCKET_NAME,
                Key=s3_key,
                ExtraArgs={"ContentType": "video/mp4"}
            )

            # Generate presigned URL
            download_url = s3.generate_presigned_url(
                ClientMethod='get_object',
                Params={'Bucket': S3_BUCKET_NAME, 'Key': s3_key},
                ExpiresIn=86400  # 24 hours
            )

            # Mark job as complete
            update_job_status(
                conn, job_id, 'complete',
                progress=100,
                download_url=download_url
            )

            logger.info(f"Video generation complete for job {job_id}: {frames_added} frames")

    except Exception as e:
        logger.error(f"Video generation failed for job {job_id}: {e}")
        update_job_status(conn, job_id, 'failed', error_message=str(e))


def process_pending_jobs(conn, s3):
    """Process any pending jobs that might have been missed."""
    cur = conn.cursor()
    cur.execute("""
        SELECT id, scan_id FROM video_jobs
        WHERE status = 'pending'
        ORDER BY created_at ASC
    """)

    pending_jobs = cur.fetchall()
    cur.close()

    if pending_jobs:
        logger.info(f"Found {len(pending_jobs)} pending jobs to process")
        for job_id, scan_id in pending_jobs:
            generate_video(conn, s3, job_id, scan_id)


def listen_for_jobs():
    """Main listener loop."""
    logger.info("Starting video generation listener...")
    logger.info(f"Database: {DATABASE_URL.split('@')[1] if '@' in DATABASE_URL else DATABASE_URL}")
    logger.info(f"S3 Endpoint: {S3_ENDPOINT}")

    conn = get_db_connection()
    s3 = get_s3_client()

    # Process any pending jobs first
    process_pending_jobs(conn, s3)

    # Start listening for notifications
    cur = conn.cursor()
    cur.execute("LISTEN video_jobs;")
    logger.info("Listening for video_jobs notifications...")

    while True:
        try:
            if select.select([conn], [], [], 5) == ([], [], []):
                continue

            conn.poll()

            while conn.notifies:
                notify = conn.notifies.pop(0)
                logger.info(f"Received notification: {notify.payload}")

                try:
                    payload = json.loads(notify.payload)
                    job_id = payload['id']
                    scan_id = payload['scan_id']

                    generate_video(conn, s3, job_id, scan_id)

                except json.JSONDecodeError as e:
                    logger.error(f"Invalid notification payload: {e}")
                except KeyError as e:
                    logger.error(f"Missing key in notification: {e}")

        except psycopg2.OperationalError as e:
            logger.error(f"Database connection lost: {e}")
            logger.info("Reconnecting in 5 seconds...")
            time.sleep(5)

            try:
                conn = get_db_connection()
                cur = conn.cursor()
                cur.execute("LISTEN video_jobs;")
                logger.info("Reconnected to database")
            except Exception as reconnect_error:
                logger.error(f"Reconnection failed: {reconnect_error}")

        except KeyboardInterrupt:
            logger.info("Shutting down...")
            break

        except Exception as e:
            logger.error(f"Unexpected error: {e}")
            time.sleep(1)

    conn.close()
    logger.info("Listener stopped")


if __name__ == "__main__":
    listen_for_jobs()
