import os
from typing import Optional

import boto3
from fastapi import HTTPException

AWS_KEY    = os.environ.get('AWS_ACCESS_KEY_ID', '')
AWS_SECRET = os.environ.get('AWS_SECRET_ACCESS_KEY', '')
S3_BUCKET  = os.environ.get('S3_BUCKET',  'mediadev-recordings')
S3_REGION  = os.environ.get('S3_REGION',  'us-east-1')


def presign(key: str, expires: int = 7200) -> Optional[str]:
    if not key or not AWS_KEY:
        return None
    try:
        s3 = boto3.client('s3', region_name=S3_REGION,
                          aws_access_key_id=AWS_KEY,
                          aws_secret_access_key=AWS_SECRET)
        return s3.generate_presigned_url('get_object',
            Params={'Bucket': S3_BUCKET, 'Key': key}, ExpiresIn=expires)
    except Exception:
        return None

def s3_put(key: str, data: bytes, content_type: str) -> str:
    """Sube bytes a S3 y devuelve la key. Lanza 503 si no hay credenciales."""
    if not AWS_KEY:
        raise HTTPException(503, 'Almacenamiento S3 no configurado')
    s3 = boto3.client('s3', region_name=S3_REGION,
                      aws_access_key_id=AWS_KEY, aws_secret_access_key=AWS_SECRET)
    s3.put_object(Bucket=S3_BUCKET, Key=key, Body=data, ContentType=content_type)
    return key
