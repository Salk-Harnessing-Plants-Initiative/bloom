import os
from supabase import create_client, Client
import boto3

supabase_url: str = os.environ.get("SUPABASE_URL")
supabase_key: str = os.environ.get("SUPABASE_KEY")
jwt_secret: str = os.environ.get("JWT_SECRET")
aws_region: str = os.environ.get("AWS_REGION")
s3_bucket_name: str = os.environ.get("S3_BUCKET_NAME")
s3_endpoint: str = os.environ.get("S3_ENDPOINT")

s3 = boto3.client(
    's3',
    region_name=aws_region,
    aws_access_key_id=os.environ.get("AWS_ACCESS_KEY_ID"),
    aws_secret_access_key=os.environ.get("AWS_SECRET_ACCESS_KEY"),
    endpoint_url=s3_endpoint 
)

supabase: Client = create_client(supabase_url, supabase_key)