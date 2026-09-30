"""生产工单与实例状态机。

工单只能引用"当前版本且四门通过"的提案版本，并在创建时钉住：

- 提案版本哈希（批准内容）
- 覆盖本次生产的各许可 id + 许可边界哈希（当前权利边界）
- 合同哈希

实例（每一件/每一批文创）状态：

    unproduced 未生产
        → in_transit 在途（已投产/已发货）
        → exhibition_promo 仅用于展览宣传（非卖分流）
    in_transit 在途
        → sold 已售
        → exhibition_promo 仅用于展览宣传（撤展剩余转宣传）
    sold 已售（终态，但保留召回/补偿标记）

撤回与改期不直接改状态，而是由 events 模块按状态生成不同处置。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .errors import StateError
from .hashing import canonical_hash


class InstanceState:
    UNPRODUCED = "unproduced"
    IN_TRANSIT = "in_transit"
    SOLD = "sold"
    EXHIBITION_PROMO = "exhibition_promo"

    TERMINAL = (SOLD, EXHIBITION_PROMO)


# 允许的状态迁移
_TRANSITIONS: dict[str, frozenset[str]] = {
    InstanceState.UNPRODUCED: frozenset({
        InstanceState.IN_TRANSIT,
        InstanceState.EXHIBITION_PROMO,
    }),
    InstanceState.IN_TRANSIT: frozenset({
        InstanceState.SOLD,
        InstanceState.EXHIBITION_PROMO,
    }),
    InstanceState.SOLD: frozenset(),
    InstanceState.EXHIBITION_PROMO: frozenset(),
}


@dataclass(frozen=True)
class RightSnapshot:
    """工单创建瞬间的权利边界快照。"""

    license_views: tuple[dict[str, Any], ...]
    snapshot_sha256: str

    @classmethod
    def of(cls, license_views: list[dict[str, Any]]) -> "RightSnapshot":
        payload = {"license_views": license_views}
        return cls(
            license_views=tuple(license_views),
            snapshot_sha256="sha256:" + canonical_hash(payload),
        )


@dataclass
class ProductionOrder:
    order_id: str
    proposal_id: str
    version: int
    version_sha256: str
    partner_id: str
    exhibition_id: str
    region: str
    channel: str
    quantity: int
    contract_sha256: str
    rights_snapshot: RightSnapshot
    license_ids: tuple[str, ...]
    created_on: str
    entry_seq: int = 0
    # 工单级冻结/退役标志（撤回处置可以冻结后续动作，但不改已售事实）
    frozen: bool = False
    retired: bool = False


@dataclass
class ProductInstance:
    instance_id: str
    order_id: str
    proposal_id: str
    version: int
    partner_id: str
    state: str = InstanceState.UNPRODUCED
    history: list[dict[str, Any]] = field(default_factory=list)
    disposition_ids: list[str] = field(default_factory=list)

    def transition(self, new_state: str, *, on_date: str, note: str = "") -> None:
        allowed = _TRANSITIONS.get(self.state, frozenset())
        if new_state not in allowed:
            raise StateError(
                f"实例 {self.instance_id} 不能从 {self.state} 迁移到 {new_state}"
            )
        self.history.append({
            "from": self.state,
            "to": new_state,
            "on_date": on_date,
            "note": note,
        })
        self.state = new_state


def required_rights_for(channels: tuple[str, ...] | list[str]) -> set[str]:
    """渠道集合决定需要哪些权利类型。"""
    from .licensing import Channel, Right

    rights = {Right.REPRODUCE}
    commercial = any(c in Channel.COMMERCIAL for c in channels)
    if commercial:
        rights.add(Right.ADAPT_COMMERCIAL)
    if Channel.EXHIBITION_PROMO in channels:
        rights.add(Right.PROMOTION)
    return rights
