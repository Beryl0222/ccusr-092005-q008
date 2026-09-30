"""艺术元素竞争锁（共享/独占模型）。

- 打样与非独占商业使用取共享锁：多个合作方可并行打样、并行生产。
- 独占许可取排他锁：与任何其他合作方持有的该元素锁冲突。
- 锁按 (合作方, 提案, 元素) 记录；撤回或退市时释放，竞争方可重新获得。

兼容矩阵（列 = 已持有，行 = 新请求）：

|          | sample | commercial | exclusive |
|----------|--------|-----------|-----------|
| sample   |   ✓    |    ✓      |    ✗      |
| commercial|  ✓    |    ✓      |    ✗      |
| exclusive |  ✗    |    ✗      |    ✗      |

同一合作方/同一提案重入不冲突。
"""
from __future__ import annotations

from dataclasses import dataclass

from .errors import ConflictError

SAMPLE = "sample"
COMMERCIAL = "commercial"
EXCLUSIVE = "exclusive"

_COMPATIBLE = {
    SAMPLE: {SAMPLE, COMMERCIAL},
    COMMERCIAL: {SAMPLE, COMMERCIAL},
    EXCLUSIVE: set(),
}


@dataclass(frozen=True)
class LockHandle:
    partner_id: str
    proposal_id: str
    element_key: str  # artwork_id/element_id
    mode: str


class ElementLockTable:
    def __init__(self) -> None:
        # element_key -> 持有的锁句柄（同一合作方可重入）
        self._held: dict[str, list[LockHandle]] = {}

    def try_acquire(
        self,
        *,
        partner_id: str,
        proposal_id: str,
        artwork_id: str,
        element_id: str,
        mode: str,
    ) -> LockHandle:
        key = f"{artwork_id}/{element_id}"
        holders = self._held.setdefault(key, [])

        # 同提案重入：幂等返回
        for h in holders:
            if h.proposal_id == proposal_id and h.partner_id == partner_id:
                return h

        mine = {h.mode for h in holders if h.partner_id == partner_id}
        mine.add(mode)
        for h in holders:
            if h.partner_id == partner_id:
                continue
            allowed = _COMPATIBLE[h.mode]
            if mode not in allowed:
                raise ConflictError(
                    self._conflict_message(key, h, mode)
                )
        handle = LockHandle(partner_id, proposal_id, key, mode)
        holders.append(handle)
        return handle

    def release_proposal(self, proposal_id: str) -> list[LockHandle]:
        released: list[LockHandle] = []
        for key, holders in self._held.items():
            keep = [h for h in holders if h.proposal_id != proposal_id]
            released.extend(h for h in holders if h.proposal_id == proposal_id)
            self._held[key] = keep
        return released

    def release(self, partner_id: str, element_keys: list[str],
                modes: tuple[str, ...], *, proposal_hint: str | None = None
                ) -> list[LockHandle]:
        """撤回许可时释放该合作方在指定元素上的指定模式锁。"""
        released: list[LockHandle] = []
        for key in element_keys:
            holders = self._held.get(key, [])
            keep: list[LockHandle] = []
            for h in holders:
                drop = (
                    h.partner_id == partner_id
                    and h.mode in modes
                    and (proposal_hint is None or h.proposal_id == proposal_hint)
                )
                if drop:
                    released.append(h)
                else:
                    keep.append(h)
            self._held[key] = keep
        return released

    def holders_of(self, artwork_id: str, element_id: str) -> list[LockHandle]:
        return list(self._held.get(f"{artwork_id}/{element_id}", []))

    @staticmethod
    def _conflict_message(key: str, held: LockHandle, wanted: str) -> str:
        # 不给后来者透露竞争方身份，只说明元素与阻断性质
        if wanted == EXCLUSIVE or held.mode == EXCLUSIVE:
            return f"艺术元素 {key} 已被独占授权，无法取得使用锁"
        return f"艺术元素 {key} 存在竞争使用冲突"
