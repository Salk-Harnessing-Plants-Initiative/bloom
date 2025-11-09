import io
import logging
import os
import tempfile

import jwt
import numpy as np
from flask import Flask, Response, jsonify, request
from jwt import DecodeError, ExpiredSignatureError, InvalidTokenError
from PIL import Image

from config import jwt_secret, s3, s3_bucket_name, supabase
from videoWriter import VideoWriter

# Configure logging
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

app = Flask(__name__)
decimate = 4


# Basic Test route 1 to check if the Flask app is running
@app.route("/")
def index() -> Response:
    return jsonify({"message": "Flask app is running!"})


# Test route to check supabase connection
@app.route("/supabaseconnection")
def get_species() -> Response | tuple[Response, int]:
    try:
        response = supabase.table("cyl_scanners").select("*").limit(5).execute()
        return jsonify(response.data)
    except Exception as e:
        logger.error("Supabase connection test failed: %s", e, exc_info=True)
        return jsonify({"error": str(e)}), 500


# Test route to check S3 connection.
@app.route("/list_buckets", methods=["GET"])
def list_buckets() -> Response | tuple[Response, int]:
    try:
        response = s3.list_buckets()
        return jsonify({"buckets": [bucket["Name"] for bucket in response["Buckets"]]})
    except Exception as e:
        logger.error("S3 list buckets failed: %s", e, exc_info=True)
        return jsonify({"error": str(e)}), 500


# Generate video from cyl_images associated with a cyl_scan
@app.route("/generate_video", methods=["POST"])
def generate_video() -> Response | tuple[Response, int]:
    # Validate JWT authentication
    headers = request.headers
    authorization = headers.get("Authorization", "")
    if not authorization.startswith("Bearer "):
        return jsonify({"message": "Unauthorized"}), 401

    _, jwt_key = authorization.split(" ", 1)
    try:
        jwt.decode(jwt_key, jwt_secret, audience="authenticated", algorithms=["HS256"])
    except (DecodeError, ExpiredSignatureError, InvalidTokenError) as e:
        logger.warning("JWT validation failed: %s", type(e).__name__)
        return jsonify({"message": "Unauthorized"}), 401

    # Validate request body
    body = request.get_json()
    if not body:
        return jsonify({"error": "Invalid JSON"}), 400

    scan_id_raw = body.get("scan_id")
    if scan_id_raw is None:
        return jsonify({"error": "Missing scan_id"}), 400

    try:
        scan_id = int(scan_id_raw)
    except (ValueError, TypeError):
        return jsonify({"error": "scan_id must be an integer"}), 400

    if scan_id <= 0:
        return jsonify({"error": "scan_id must be positive"}), 400

    try:
        scan = (
            supabase.table("cyl_scans")
            .select("*, cyl_images(*)")
            .eq("id", scan_id)
            .execute()
        )

        if not scan.data:
            return jsonify({"error": "Scan not found"}), 404

        cyl_images = sorted(
            scan.data[0]["cyl_images"],
            key=lambda x: x["frame_number"],  # type: ignore[index, arg-type, call-overload]
        )

        with tempfile.TemporaryDirectory() as tmp_dir:
            video_path = os.path.join(tmp_dir, f"{scan_id}.mp4")
            video_writer = VideoWriter(filename=video_path)

            frames_added = 0
            images_path = "storage-single-tenant/images"

            for cyl_image in cyl_images:
                object_prefix = f"{images_path}/{cyl_image['object_path']}"
                response = s3.list_objects_v2(
                    Bucket=s3_bucket_name, Prefix=object_prefix
                )
                if "Contents" not in response or len(response["Contents"]) != 1:
                    continue

                object_path = response["Contents"][0]["Key"]
                obj = s3.get_object(Bucket=s3_bucket_name, Key=object_path)
                image_bytes = obj["Body"].read()
                image_array = np.array(Image.open(io.BytesIO(image_bytes)))
                image_array = image_array[::decimate, ::decimate]

                if image_array.size == 0:
                    continue

                video_writer.add(image_array)
                frames_added += 1

            video_writer.close()

            if not os.path.exists(video_path):
                return jsonify({"error": "Video file was not created"}), 500

            s3_key = f"cyl-videos/{scan_id}.mp4"
            s3.upload_file(
                Filename=video_path,
                Bucket=s3_bucket_name,
                Key=s3_key,
                ExtraArgs={"ContentType": "video/mp4"},
            )

            url = s3.generate_presigned_url(
                ClientMethod="get_object",
                Params={"Bucket": s3_bucket_name, "Key": s3_key},
                ExpiresIn=3600,  # 1 hour
            )

        return jsonify(
            {
                "message": "Video generated successfully",
                "scan_id": scan_id,
                "total_frames": len(cyl_images),
                "download_url": url,
            }
        )

    except Exception as e:
        logger.error(
            "Video generation failed for scan_id %s: %s", scan_id, e, exc_info=True
        )
        return jsonify({"error": str(e)}), 500


# Helper function to get presigned URL for a given object path
def get_presigned_url(object_path: str | None) -> str | None:
    if not object_path:
        return None

    object_prefix = object_path
    logger.debug("Generating presigned URL for object path: %s", object_prefix)
    try:
        response = s3.list_objects_v2(Bucket=s3_bucket_name, Prefix=object_prefix)

        if "Contents" not in response or not response["Contents"]:
            logger.debug("No S3 objects found for prefix: %s", object_prefix)
            return None

        s3_path = response["Contents"][0]["Key"]

        url: str = s3.generate_presigned_url(
            "get_object",
            Params={"Bucket": s3_bucket_name, "Key": s3_path},
            ExpiresIn=3600,
        )
        return url
    except Exception as e:
        logger.error("Error generating presigned URL for %s: %s", object_path, e)
        return None


# Route to generate presigned URLs for given object paths
@app.route("/get_presigned_urls", methods=["POST"])
def generate_presigned_urls() -> Response | tuple[Response, int]:
    logger.debug("Received request for presigned URLs")
    headers = request.headers
    authorization = headers.get("Authorization", "")
    if not authorization.startswith("Bearer "):
        return jsonify({"message": "Unauthorized"}), 401

    _, jwt_key = authorization.split(" ", 1)
    try:
        jwt.decode(jwt_key, jwt_secret, audience="authenticated", algorithms=["HS256"])
    except (DecodeError, ExpiredSignatureError, InvalidTokenError) as e:
        logger.warning("JWT validation failed: %s", type(e).__name__)
        return jsonify({"message": "Unauthorized"}), 401

    body = request.get_json() or {}
    object_paths = body.get("object_paths", [])

    presigned_urls = []
    skipped_paths = []
    invalid_urls = []

    for object_path in object_paths:
        logger.debug("Processing object path: %s", object_path)
        if object_path:
            url = get_presigned_url(object_path)
            if url:
                presigned_urls.append(url)
                invalid_urls.append(False)
            else:
                presigned_urls.append("")
                invalid_urls.append(True)
                skipped_paths.append(object_path)
        else:
            presigned_urls.append("")
            invalid_urls.append(True)
            skipped_paths.append(object_path)

    return jsonify(
        {
            "presigned_urls": presigned_urls,
            "invalid_urls": invalid_urls,
            "skipped_paths": skipped_paths,
        }
    )


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5002, debug=True)
