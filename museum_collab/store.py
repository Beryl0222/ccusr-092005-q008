"""只追加事件日志 + 哈希链 + 乐观并发控制。

这是协作后端的底座：
- 任何状态变化都是一条不可变事件，顺序追加；
- 每条事件携带内容哈希与前序链哈希，旧决定只能被新事件“取代/补充”，
  永远不会被静默覆盖（满足“不得一刀切覆盖旧决定”）；
- 每个提案流维护一个单调版本号，并发提交基于 expected_version 做乐观锁，
  两个合作方同时竞争同一元素、并行打样同时推进都能被确定性地裁决。
"""
from __future__ import annotations

import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable

from .codec import chain_hash, content_hash, genesis


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass(frozen=True)
class Event:
    seq: int
    stream: str
    version: int          # 该流内的单调版本（从 1 开始）
    at: str
    actor: str
    type: str
    payload: dict[str, Any]
    content_digest: str   # 固定 payload（批准内容）
    chain: str            # 链式哈希，固定日志顺序与完整性

    def to_dict(self) -> dict[str, Any]:
        return {
            "seq": self.seq,
            "stream": self.stream,
            "version": self.version,
            "at": self.at,
            "actor": self.actor,
            "type": self.type,
            "payload": self.payload,
            "content_digest": self.content_digest,
            "chain": self.chain,
        }


class ConcurrencyError(Exception):
    """流版本与期望不符（并发冲突）。"""


class EventStore:
    def __init__(self, clock: Callable[[], str] = utcnow) -> None:
        self._events: list[Event] = []
        self._streams: dict[str, list[Event]] = {}
        self._lock = threading.RLock()
        self._clock = clock

    # ---- 写入 ----------------------------------------------------------
    def append(
        self,
        stream: str,
        event_type: str,
        payload: dict[str, Any],
        *,
        actor: str,
        expected_version: int,
        at: str | None = None,
    ) -> Event:
        with self._lock:
            history = self._streams.setdefault(stream, [])
            current = len(history)
            if expected_version != current:
                raise ConcurrencyError(
                    f"流 {stream} 版本冲突：期望 {expected_version}，实际 {current}"
                )
            prev = history[-1].chain if history else genesis()
            ts = at or self._clock()
            digest = content_hash(payload)
            new_version = current + 1
            chain = chain_hash(prev, event_type, ts, actor, payload)
            event = Event(
                seq=len(self._events),
                stream=stream,
                version=new_version,
                at=ts,
                actor=actor,
                type=event_type,
                payload=payload,
                content_digest=digest,
                chain=chain,
            )
            self._events.append(event)
            history.append(event)
            return event

    # ---- 读取 ----------------------------------------------------------
    def stream_version(self, stream: str) -> int:
        with self._lock:
            return len(self._streams.get(stream, ()))

    def read_stream(self, stream: str) -> list[Event]:
        with self._lock:
            return list(self._streams.get(stream, ()))

    def read_all(self) -> list[Event]:
        with self._lock:
            return list(self._events)

    # ---- 完整性校验 ----------------------------------------------------
    def verify_chain(self) -> None:
        """重放整条日志，校验内容哈希与链式哈希，发现任何篡改即报错。"""
        with self._lock:
            prev_by_stream: dict[str, str] = {}
            for e in self._events:
                if e.content_digest != content_hash(e.payload):
                    raise IntegrityError(f"事件 {e.seq} 内容哈希不一致")
                prev = prev_by_stream.get(e.stream, genesis())
                if e.chain != chain_hash(prev, e.type, e.at, e.actor, e.payload):
                    raise IntegrityError(f"事件 {e.seq} 链式哈希不一致")
                prev_by_stream[e.stream] = e.chain


class IntegrityError(Exception):
    """日志被篡改，哈希校验失败。"""
