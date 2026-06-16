"""Thin S3 / MinIO client wrapper.

Effective config = DB (app_config.data.s3, super-admin editable in the UI) over
env defaults — so storage can be configured entirely from Settings → Storage, or
via env for headless deploys. Works against AWS S3 or any S3-compatible store
(MinIO) via endpoint_url + path-style.

Used by:
  - storage.py        — durable inbox/processed/failed lifecycle (backend=s3)
  - ingest/render     — page-image upload (pages/doc_<id>/<n>.png)
  - s3_import.py      — bulk import: list a source prefix, pull files to ingest

The cached boto3 client is rebuilt whenever the config signature changes (call
reset() after saving config).
"""
from __future__ import annotations

import threading

from . import appcfg

_client = None
_sig = None
_lock = threading.Lock()


def cfg() -> dict:
    """Effective S3 settings (DB over env), secret included (internal)."""
    return appcfg.get_s3(reveal=True)


def backend() -> str:
    return cfg().get("backend", "local")


def enabled() -> bool:
    """True if S3 is the backend OR a bucket is configured (import can run even
    when the durable backend is still local)."""
    c = cfg()
    return bool(c.get("bucket"))


def bucket() -> str:
    return cfg().get("bucket", "")


def _norm_prefix(p: str) -> str:
    p = (p or "").strip().lstrip("/")
    if p and not p.endswith("/"):
        p += "/"
    return p


def prefix() -> str:
    return _norm_prefix(cfg().get("prefix", "docsensei/"))


def import_prefix() -> str:
    return _norm_prefix(cfg().get("import_prefix", "inbox-drop/"))


def prefixed(key: str) -> str:
    """Namespace a key under our managed prefix (idempotent)."""
    p = prefix()
    key = key.lstrip("/")
    return key if (p and key.startswith(p)) else f"{p}{key}"


def reset() -> None:
    """Drop the cached client so the next call rebuilds from fresh config."""
    global _client, _sig
    with _lock:
        _client = None
        _sig = None


def client():
    """Lazily build (and cache) the boto3 S3 client for the current config."""
    c = cfg()
    sig = (c.get("region"), c.get("endpoint_url"), c.get("access_key_id"),
           c.get("secret_access_key"), c.get("force_path_style"))
    if _client is not None and sig == _sig:
        return _client
    return _build(c, sig)


def _build(c: dict, sig):
    global _client, _sig
    import boto3
    from botocore.config import Config
    cfgo = Config(
        region_name=c.get("region") or "us-east-1",
        s3={"addressing_style": "path" if c.get("force_path_style") else "auto"},
        retries={"max_attempts": 3, "mode": "standard"},
    )
    kwargs = {"config": cfgo}
    if c.get("endpoint_url"):
        kwargs["endpoint_url"] = c["endpoint_url"]
    if c.get("access_key_id") and c.get("secret_access_key"):
        kwargs["aws_access_key_id"] = c["access_key_id"]
        kwargs["aws_secret_access_key"] = c["secret_access_key"]
    with _lock:
        _client = boto3.client("s3", **kwargs)
        _sig = sig
        return _client


def ensure_bucket() -> None:
    """Create the bucket if missing (no-op if it exists / not configured)."""
    if not enabled():
        return
    b = bucket()
    c = client()
    try:
        c.head_bucket(Bucket=b)
    except Exception:
        try:
            c.create_bucket(Bucket=b)
            print(f"[s3] created bucket {b}")
        except Exception as e:
            print(f"[s3] ensure_bucket: {e!r}")


def test_connection() -> dict:
    """Try to reach the bucket — for the Settings 'Test connection' button."""
    if not enabled():
        return {"ok": False, "detail": "no bucket configured"}
    try:
        client().head_bucket(Bucket=bucket())
        return {"ok": True, "detail": f"reached bucket '{bucket()}'"}
    except Exception:
        # bucket may not exist yet — try to create it (we own the namespace)
        try:
            client().create_bucket(Bucket=bucket())
            return {"ok": True, "detail": f"created bucket '{bucket()}'"}
        except Exception as e:
            return {"ok": False, "detail": f"{type(e).__name__}: {e}"}


# ---- object ops (keys are bucket-relative) ----

def put_file(local_path, key: str, content_type: str | None = None) -> None:
    extra = {"ContentType": content_type} if content_type else None
    client().upload_file(str(local_path), bucket(), key, ExtraArgs=extra)


def put_fileobj(fileobj, key: str) -> None:
    client().upload_fileobj(fileobj, bucket(), key)


def download_to(key: str, local_path) -> None:
    client().download_file(bucket(), key, str(local_path))


def get_bytes(key: str) -> bytes:
    return client().get_object(Bucket=bucket(), Key=key)["Body"].read()


def get_stream(key: str):
    obj = client().get_object(Bucket=bucket(), Key=key)
    return obj["Body"], obj.get("ContentType")


def presign_enabled() -> bool:
    return bool(cfg().get("presign"))


def presign_get(key: str, expires: int = 3600) -> str:
    """Presigned GET URL so the browser pulls the object straight from S3."""
    return client().generate_presigned_url(
        "get_object", Params={"Bucket": bucket(), "Key": key}, ExpiresIn=expires)


def exists(key: str) -> bool:
    try:
        client().head_object(Bucket=bucket(), Key=key)
        return True
    except Exception:
        return False


def copy(src_key: str, dst_key: str) -> None:
    client().copy_object(Bucket=bucket(),
                         CopySource={"Bucket": bucket(), "Key": src_key},
                         Key=dst_key)


def delete(key: str) -> None:
    try:
        client().delete_object(Bucket=bucket(), Key=key)
    except Exception as e:
        print(f"[s3] delete {key}: {e!r}")


def list_keys(prefix_: str) -> list[str]:
    out: list[str] = []
    paginator = client().get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket(), Prefix=prefix_):
        for obj in page.get("Contents", []) or []:
            k = obj["Key"]
            if not k.endswith("/"):
                out.append(k)
    return out
