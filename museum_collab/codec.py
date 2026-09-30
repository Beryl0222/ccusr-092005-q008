"""规范化编码与内容哈希。

所有需要被“固定”的内容（设计版本、批准决定、合同正文、沟通消息）都先转成
排序键的规范化 JSON，再计算 SHA-256。哈希跟随事件落盘，事后任何对内容的
篡改都会导致重放哈希不一致。
"""
from __future__ import annotations

import hashlib
import json
from typing import Any

_GENESIS = "sha256:genesis"


def canonical(obj: Any) -> bytes:
    """转成字节级稳定的规范化 JSON（键排序、无空白、保留非 ASCII）。"""
    return json.dumps(
        obj,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=_json_default,
    ).encode("utf-8")


def _json_default(obj: Any) -> Any:
    if hasattr(obj, "to_dict"):
        return obj.to_dict()
    if isinstance(obj, (set, frozenset)):
        return sorted(obj)
    raise TypeError(f"无法规范化 {type(obj)!r}")


def content_hash(obj: Any) -> str:
    return "sha256:" + hashlib.sha256(canonical(obj)).hexdigest()


def chain_hash(prev_hash: str, event_type: str, at: str, actor: str, payload: Any) -> str:
    """事件链式哈希：把上一事件哈希纳入计算，整条日志不可静默改写。"""
    return content_hash(
        {
            "prev": prev_hash or _GENESIS,
            "type": event_type,
            "at": at,
            "actor": actor,
            "payload": payload,
        }
    )


def genesis() -> str:
    return _GENESIS
