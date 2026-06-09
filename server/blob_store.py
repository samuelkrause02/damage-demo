"""Vercel Blob: Verträge (JSON) und Schadenfotos."""

from __future__ import annotations

import json
import os
from typing import Any

BLOB_PREFIX = "damage-demo"


def is_configured() -> bool:
    return bool(
        os.getenv("BLOB_READ_WRITE_TOKEN")
        or (os.getenv("BLOB_STORE_ID") and os.getenv("VERCEL_OIDC_TOKEN"))
    )


def contract_path(contract_id: str) -> str:
    return f"{BLOB_PREFIX}/contracts/{contract_id}.json"


def photo_path(contract_id: str, damage_number: int) -> str:
    return f"{BLOB_PREFIX}/photos/{contract_id}/{damage_number}.jpg"


def put_bytes(path: str, data: bytes, content_type: str) -> str:
    from vercel.blob import put

    result = put(
        path,
        data,
        access="public",
        content_type=content_type,
        overwrite=True,
    )
    return result.url


def get_bytes(url_or_path: str) -> bytes | None:
    from vercel.blob import BlobNotFoundError, get

    try:
        return get(url_or_path).content
    except BlobNotFoundError:
        return None
    except Exception:
        return None


def put_json(path: str, data: Any) -> str:
    body = json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")
    return put_bytes(path, body, "application/json")


def get_json(path: str) -> Any | None:
    raw = get_bytes(path)
    if raw is None:
        return None
    return json.loads(raw.decode("utf-8"))
