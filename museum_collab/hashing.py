"""内容寻址工具：批准内容用哈希固定。

所有哈希都走规范化 JSON，避免字段顺序或空白导致同物不同哈希。
"""
from __future__ import annotations

import hashlib
import json
from typing import Any


def canonical_json(payload: Any) -> str:
    return json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )


def canonical_hash(payload: Any) -> str:
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def text_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def short(h: str, length: int = 12) -> str:
    return h[:length]
