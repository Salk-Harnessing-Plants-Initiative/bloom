import os
from typing import cast

import boto3
from supabase import Client, create_client  # type: ignore[attr-defined]

supabase_url: str = cast(str, os.environ.get("SUPABASE_URL"))
supabase_key: str = cast(str, os.environ.get("SUPABASE_KEY"))
jwt_secret: str = cast(str, os.environ.get("JWT_SECRET"))
aws_region: str = cast(str, os.environ.get("AWS_REGION"))
s3_bucket_name: str = cast(str, os.environ.get("S3_BUCKET_NAME"))
s3_endpoint: str = cast(str, os.environ.get("S3_ENDPOINT"))

s3 = boto3.client(
    "s3",
    region_name=aws_region,
    aws_access_key_id=os.environ.get("AWS_ACCESS_KEY_ID"),
    aws_secret_access_key=os.environ.get("AWS_SECRET_ACCESS_KEY"),
    endpoint_url=s3_endpoint,
)

supabase: Client = create_client(supabase_url, supabase_key)
