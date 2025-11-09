import os

import boto3
from supabase import Client, create_client  # type: ignore[attr-defined]


def get_required_env(key: str) -> str:
    """Get required environment variable or raise clear error."""
    value = os.environ.get(key)
    if value is None:
        raise ValueError(
            f"Required environment variable {key} is not set. "
            f"Please check your .env file or environment configuration."
        )
    return value


supabase_url: str = get_required_env("SUPABASE_URL")
supabase_key: str = get_required_env("SUPABASE_KEY")
jwt_secret: str = get_required_env("JWT_SECRET")
aws_region: str = get_required_env("AWS_REGION")
s3_bucket_name: str = get_required_env("S3_BUCKET_NAME")
s3_endpoint: str = get_required_env("S3_ENDPOINT")

s3 = boto3.client(
    "s3",
    region_name=aws_region,
    aws_access_key_id=os.environ.get("AWS_ACCESS_KEY_ID"),
    aws_secret_access_key=os.environ.get("AWS_SECRET_ACCESS_KEY"),
    endpoint_url=s3_endpoint,
)

supabase: Client = create_client(supabase_url, supabase_key)
