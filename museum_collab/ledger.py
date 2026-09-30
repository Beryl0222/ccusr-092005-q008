"""只追加、哈希成链的台账。

每条记录保存前一条记录的哈希与自身载荷哈希，形成链式结构：
任何对历史记录的插入、删除或修改都会让链校验失败。
沟通记录、审批决定、版本冻结、许可、生产、处置全部落账，
策展人的回溯与合作方看到的"沟通摘要"都来自同一份账。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from itertools import count
from typing import Any

from .errors import LedgerError
from .hashing import canonical_hash

# 台账记录类型，集中声明，避免散落的魔法字符串。
ENTRY_KINDS = (
    "artwork_registered",
    "partner_registered",
    "exhibition_registered",
    "contract_recorded",
    "message_posted",
    "proposal_created",
    "proposal_revised",
    "proposal_frozen",
    "approval_granted",
    "approval_invalidated",
    "license_granted",
    "license_bound",
    "license_revoked",
    "sample_started",
    "conflict_blocked",
    "element_lock_acquired",
    "element_lock_released",
    "production_order_created",
    "order_resumed",
    "instance_status_changed",
    "schedule_changed",
    "disposition_issued",
)


@dataclass(frozen=True)
class Entry:
    seq: int
    kind: str
    actor: str
    payload: dict[str, Any]
    prev_hash: str
    hash: str
    timestamp: str  # ISO8601，由时钟回调注入，测试可确定化


class Clock:
    """单调时钟；生产环境换成真实时间源。"""

    def __init__(self) -> None:
        self._ticks = count(1)

    def now(self) -> str:
        return f"t{next(self._ticks):06d}"


class Ledger:
    GENESIS = "sha256:0" * 1  # 占位前缀，实际值见下

    def __init__(self, clock: Clock | None = None) -> None:
        self._entries: list[Entry] = []
        self._clock = clock or Clock()
        self._genesis = "sha256:" + "0" * 64

    @property
    def entries(self) -> list[Entry]:
        return list(self._entries)

    def head_hash(self) -> str:
        return self._entries[-1].hash if self._entries else self._genesis

    def append(self, kind: str, actor: str, payload: dict[str, Any]) -> Entry:
        if kind not in ENTRY_KINDS:
            raise ValueError(f"未知台账记录类型: {kind}")
        prev = self.head_hash()
        ts = self._clock.now()
        body = {
            "kind": kind,
            "actor": actor,
            "payload": payload,
            "prev_hash": prev,
            "timestamp": ts,
        }
        entry = Entry(
            seq=len(self._entries) + 1,
            kind=kind,
            actor=actor,
            payload=payload,
            prev_hash=prev,
            timestamp=ts,
            hash="sha256:" + canonical_hash(body),
        )
        self._entries.append(entry)
        return entry

    def verify(self) -> None:
        """重放整条链，发现篡改即抛 LedgerError。"""
        prev = self._genesis
        for entry in self._entries:
            if entry.prev_hash != prev:
                raise LedgerError(f"第 {entry.seq} 条记录的前驱哈希断裂")
            body = {
                "kind": entry.kind,
                "actor": entry.actor,
                "payload": entry.payload,
                "prev_hash": entry.prev_hash,
                "timestamp": entry.timestamp,
            }
            if entry.hash != "sha256:" + canonical_hash(body):
                raise LedgerError(f"第 {entry.seq} 条记录载荷哈希不符")
            prev = entry.hash

    def by_kind(self, *kinds: str) -> list[Entry]:
        return [e for e in self._entries if e.kind in kinds]

    def filter(self, predicate) -> list[Entry]:
        return [e for e in self._entries if predicate(e)]
