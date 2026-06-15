"""Thin S3 / MinIO client wrapper.

One boto3 client built from config (works against AWS S3 or any S3-compatible
store like MinIO via S3_ENDPOINT_URL + path-style). Used by:
  - storage.py        — durable inbox/processed/failed lifecycle (STORAGE=s3)
  - ingest/render     — page-image upload (pages/doc_<id>/<n>.png)
  - s3_import.py      — bulk import: list a source prefix, pull files to ingest

All keys are bucket-relative strings. OUR managed files live under S3_PREFIX;
the import source lives under S3_IMPORT_PREFIX (configurable, can differ).
Fail-soft helpers raise on real errors so callers can decide; `enabled()` lets
callers gate cleanly.
"""
from __future__ import annotations

import threading

from .config import (
    S3_BUCKET, S3_REGION, S3_ENDPOINT_URL,
    S3_ACCESS_KEY_ID, S3_SECRET_ACCESS_KEY, S3_PREFIX, S3_FORCE_PATH_STYLE,
)

_client = None
_lock = threading.Lock()


def enabled() -> bool:
    """True if a bucket is configured (client can be built)."""
    return bool(S3_BUCKET)


def bucket() -> str:
    return S3_BUCKET


def prefixed(key: str) -> str:
    """Namespace a key under our managed prefix (idempotent if already prefixed)."""
    p = S3_PREFIX
    if p and not p.endswith("/"):
        p += "/"
    key = key.lstrip("/")
    return key if (p and key.startswith(p)) else f"{p}{key}"


def client():
    """Lazily build (and cache) the boto3 S3 client."""
    global _client
    if _client is not None:
        return _client
    with _lock:
        if _client is not None:
            return _client
        import boto3
        from botocore.config import Config
        cfg = Config(
            region_name=S3_REGION,
            s3={"addressing_style": "path" if S3_FORCE_PATH_STYLE else "auto"},
            retries={"max_attempts": 3, "mode": "standard"},
        )
        kwargs = {"config": cfg}
        if S3_ENDPOINT_URL:
            kwargs["endpoint_url"] = S3_ENDPOINT_URL
        if S3_ACCESS_KEY_ID and S3_SECRET_ACCESS_KEY:
            kwargs["aws_access_key_id"] = S3_ACCESS_KEY_ID
            kwargs["aws_secret_access_key"] = S3_SECRET_ACCESS_KEY
        _client = boto3.client("s3", **kwargs)
        return _client


def ensure_bucket() -> None:
    """Create the bucket if missing (no-op if it exists / not configured)."""
    if not enabled():
        return
    c = client()
    try:
        c.head_bucket(Bucket=S3_BUCKET)
    except Exception:
        try:
            c.create_bucket(Bucket=S3_BUCKET)
            print(f"[s3] created bucket {S3_BUCKET}")
        except Exception as e:
            print(f"[s3] ensure_bucket: {e!r}")


# ---- object ops (keys are bucket-relative, caller decides namespacing) ----

def put_file(local_path, key: str, content_type: str | None = None) -> None:
    extra = {"ContentType": content_type} if content_type else None
    client().upload_file(str(local_path), S3_BUCKET, key, ExtraArgs=extra)


def put_fileobj(fileobj, key: str) -> None:
    client().upload_fileobj(fileobj, S3_BUCKET, key)


def download_to(key: str, local_path) -> None:
    client().download_file(S3_BUCKET, key, str(local_path))


def get_bytes(key: str) -> bytes:
    return client().get_object(Bucket=S3_BUCKET, Key=key)["Body"].read()


def get_stream(key: str):
    """Return (StreamingBody, content_type) for serving."""
    obj = client().get_object(Bucket=S3_BUCKET, Key=key)
    return obj["Body"], obj.get("ContentType")


def exists(key: str) -> bool:
    try:
        client().head_object(Bucket=S3_BUCKET, Key=key)
        return True
    except Exception:
        return False


def copy(src_key: str, dst_key: str) -> None:
    client().copy_object(Bucket=S3_BUCKET,
                         CopySource={"Bucket": S3_BUCKET, "Key": src_key},
                         Key=dst_key)


def delete(key: str) -> None:
    try:
        client().delete_object(Bucket=S3_BUCKET, Key=key)
    except Exception as e:
        print(f"[s3] delete {key}: {e!r}")


def list_keys(prefix: str) -> list[str]:
    """All object keys under a prefix (paginated)."""
    out: list[str] = []
    paginator = client().get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=S3_BUCKET, Prefix=prefix):
        for obj in page.get("Contents", []) or []:
            k = obj["Key"]
            if not k.endswith("/"):      # skip folder markers
                out.append(k)
    return out
