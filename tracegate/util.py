"""Small deterministic helpers with no environment-specific state."""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from datetime import datetime, timezone
from typing import Any


ACN_RE = re.compile(r"^[A-Z0-9][A-Z0-9._-]{2,63}$")
KPSN_RE = re.compile(r"^[A-Z0-9][A-Z0-9._:/-]{2,127}$")
REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{7,127}$")
MAC_COMPACT_RE = re.compile(r"^[0-9A-Fa-f]{12}$")
MAC_COLON_RE = re.compile(r"^(?:[0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}$")
MAC_HYPHEN_RE = re.compile(r"^(?:[0-9A-Fa-f]{2}-){5}[0-9A-Fa-f]{2}$")
MAC_CISCO_RE = re.compile(r"^(?:[0-9A-Fa-f]{4}\.){2}[0-9A-Fa-f]{4}$")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace(
        "+00:00", "Z"
    )


def canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def stable_hash(*parts: str, length: int = 32) -> str:
    raw = "|".join(parts).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:length]


def payload_hash(operation: str, payload: dict[str, Any]) -> str:
    return stable_hash("tracegate-request-v2", operation, canonical_json(payload), length=64)


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


def normalize_acn(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    return value.strip().upper()


def normalize_kpsn(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    return value.strip().upper()


def normalize_mac(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    if MAC_COMPACT_RE.fullmatch(value):
        compact = value
    elif MAC_COLON_RE.fullmatch(value):
        compact = value.replace(":", "")
    elif MAC_HYPHEN_RE.fullmatch(value):
        compact = value.replace("-", "")
    elif MAC_CISCO_RE.fullmatch(value):
        compact = value.replace(".", "")
    else:
        return ""
    compact = compact.upper()
    return ":".join(compact[index : index + 2] for index in range(0, 12, 2))


def require_request_id(value: Any) -> str:
    if not isinstance(value, str) or not REQUEST_ID_RE.fullmatch(value.strip()):
        return ""
    return value.strip()
