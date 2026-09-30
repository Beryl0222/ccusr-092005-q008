"""领域枚举与值对象。

角色、审核门、变更面及其影响矩阵集中在这里，是整套流程规则的单一事实来源。
"""
from __future__ import annotations

import enum
from dataclasses import dataclass, field


class Role(str, enum.Enum):
    CURATOR = "curator"            # 策展人 / 学术
    RIGHTS_OFFICER = "rights"      # 权利确认（法务/版权）
    PUBLIC_BENEFIT_OFFICER = "benefit"  # 公益属性判断
    BUSINESS_OFFICER = "business"  # 经营审批
    PRODUCER = "producer"          # 普通运营 / 生产
    PARTNER = "partner"            # 外部合作设计方
    CURATOR_AUDIT = "curator_audit"  # 策展人溯源视图（拿到成品时回查）


class Gate(str, enum.Enum):
    """四道审核门，分别由对应角色独立完成。"""

    ACADEMIC = "academic"   # 学术审核：元素引用、修改方式是否忠于原作
    RIGHTS = "rights"       # 权利确认：复制与商业改编是否获权利人许可
    BENEFIT = "benefit"     # 公益属性判断：是否属于公益用途/例外
    BUSINESS = "business"   # 经营审批：渠道、数量、定价、合同


GATE_ROLE: dict[Gate, Role] = {
    Gate.ACADEMIC: Role.CURATOR,
    Gate.RIGHTS: Role.RIGHTS_OFFICER,
    Gate.BENEFIT: Role.PUBLIC_BENEFIT_OFFICER,
    Gate.BUSINESS: Role.BUSINESS_OFFICER,
}

ALL_GATES: tuple[Gate, ...] = tuple(Gate)


class Stage(str, enum.Enum):
    """提案主生命周期。"""

    DRAFT = "draft"
    IN_REVIEW = "in_review"
    APPROVED = "approved"        # 四门全过，可签约/打样/量产
    SUPERSEDED = "superseded"    # 被新版本取代（旧版本决定保留不被覆盖）
    SUSPENDED = "suspended"      # 权利撤回或改期冲击，等待按实例处置
    DELISTED = "delisted"        # 退市


class ChangeFacet(str, enum.Enum):
    """设计/经营方案中可变的“面”，决定需要重启哪些门。"""

    ARTWORK_REF = "artwork_ref"  # 换作品 / 换引用元素
    ADAPTATION = "adaptation"    # 图案、改编方式变化
    MATERIAL = "material"        # 材质变化
    CHANNEL = "channel"          # 销售渠道变化
    QUANTITY = "quantity"        # 数量变化（许可上限内）
    TERM = "term"                # 期限/展期窗口变化
    REGION = "region"            # 地区变化
    PURPOSE = "purpose"          # 公益/商业属性变化
    MINOR_TEXT = "minor_text"    # 不触及权利与图案的文案订正


@dataclass(frozen=True)
class FacetImpact:
    """某个面变更后的影响：重启的门 + 是否使既有许可失效 + 是否必须新建版本。"""

    reopen: frozenset[Gate]
    license_invalidated: bool = False
    requires_new_version: bool = True


# 变更影响矩阵：只重启“必要”的审核，其余门的批准沿用到新版本。
FACET_IMPACT: dict[ChangeFacet, FacetImpact] = {
    ChangeFacet.ARTWORK_REF: FacetImpact(
        frozenset({Gate.ACADEMIC, Gate.RIGHTS, Gate.BENEFIT, Gate.BUSINESS}),
        license_invalidated=True,
    ),
    ChangeFacet.ADAPTATION: FacetImpact(
        frozenset({Gate.ACADEMIC, Gate.RIGHTS, Gate.BUSINESS}),
        license_invalidated=True,
    ),
    ChangeFacet.MATERIAL: FacetImpact(
        frozenset({Gate.ACADEMIC, Gate.RIGHTS}),
    ),
    ChangeFacet.CHANNEL: FacetImpact(
        frozenset({Gate.RIGHTS, Gate.BUSINESS}),
    ),
    ChangeFacet.REGION: FacetImpact(
        frozenset({Gate.RIGHTS, Gate.BUSINESS}),
        license_invalidated=True,
    ),
    ChangeFacet.QUANTITY: FacetImpact(
        frozenset({Gate.BUSINESS}),
    ),
    ChangeFacet.TERM: FacetImpact(
        frozenset({Gate.RIGHTS, Gate.BUSINESS}),
    ),
    ChangeFacet.PURPOSE: FacetImpact(
        frozenset({Gate.BENEFIT, Gate.RIGHTS, Gate.BUSINESS}),
        license_invalidated=True,
    ),
    ChangeFacet.MINOR_TEXT: FacetImpact(frozenset(), requires_new_version=True),
}


class RefMode(str, enum.Enum):
    """元素使用方式：仅复制，或包含商业改编。"""

    REPRODUCTION = "reproduction"
    COMMERCIAL_ADAPTATION = "commercial_adaptation"


class Purpose(str, enum.Enum):
    COMMERCIAL = "commercial"
    PUBLIC_BENEFIT = "public_benefit"   # 公益
    EXHIBITION_PROMO = "exhibition_promo"  # 仅展览宣传


class ReservationMode(str, enum.Enum):
    """对同一艺术元素的竞争占用方式。"""

    NONE = "none"              # 不预留（并行打样互不阻断）
    PARALLEL_SAMPLE = "parallel_sample"  # 并行打样：可并行，转量产时竞争
    EXCLUSIVE_OPTION = "exclusive_option"  # 排他意向：占用期内阻断他方


class InstanceState(str, enum.Enum):
    """实物/权益实例状态——撤回与改期绝不一刀切，按状态分别处置。"""

    NOT_PRODUCED = "not_produced"    # 未生产
    IN_TRANSIT = "in_transit"        # 在途
    SOLD = "sold"                    # 已售
    PROMO_ONLY = "promo_only"        # 仅用于展览宣传


class Trigger(str, enum.Enum):
    RIGHTS_WITHDRAWN = "rights_withdrawn"  # 权利人撤回
    OPENING_RESCHEDULED = "opening_rescheduled"  # 开幕改期
    LICENSE_EXPIRY = "license_expiry"      # 许可到期
    MANUAL_DELIST = "manual_delist"        # 经营决定退市


class DispositionAction(str, enum.Enum):
    """每个实例在触发事件下得到的具体处置。"""

    CANCEL_BEFORE_PRODUCTION = "cancel_before_production"  # 未生产：立即取消
    RECALL_OR_HOLD = "recall_or_hold"      # 在途：召回/暂扣，停止继续发货
    HONOR_SOLD_COPIES = "honor_sold_copies"  # 已售：不追溯已售，停止再售
    PROMO_CONTINUE = "promo_continue"      # 展宣品：宣传用途可继续
    PROMO_HALT = "promo_halt"              # 展宣品：撤回即停用宣传
    RESCHEDULE_HOLD = "reschedule_hold"    # 改期：暂停，按新档期重排，不销毁
    RESCHEDULE_REVALIDATE = "reschedule_revalidate"  # 改期：须在新档期前复核许可期限


@dataclass(frozen=True)
class LicenseScope:
    """许可四维：数量、地区、渠道、期限（外加用途与元素绑定）。"""

    max_quantity: int
    regions: frozenset[str]
    channels: frozenset[str]
    valid_from: str
    valid_until: str
    purpose: Purpose

    def covers(
        self,
        *,
        quantity: int,
        region: str,
        channel: str,
        on_date: str,
        purpose: Purpose,
    ) -> bool:
        return (
            quantity <= self.max_quantity
            and region in self.regions
            and channel in self.channels
            and self.valid_from <= on_date <= self.valid_until
            and purpose == self.purpose
        )


@dataclass(frozen=True)
class ElementRef:
    """设计提案对“哪件作品的哪些元素、以何种方式使用”的明确标注。"""

    artwork_id: str
    element_ids: tuple[str, ...]
    mode: RefMode
    note: str = ""


@dataclass
class DesignContent:
    """被哈希固定的设计方案内容。"""

    artwork_refs: tuple[ElementRef, ...]
    material: str
    channels: tuple[str, ...]
    regions: tuple[str, ...]
    quantity: int
    purpose: Purpose
    promo_only: bool = False
    spec: dict = field(default_factory=dict)

    def facet_view(self) -> dict:
        return {
            "artwork_refs": [
                {
                    "artwork_id": r.artwork_id,
                    "element_ids": list(r.element_ids),
                    "mode": r.mode.value,
                    "note": r.note,
                }
                for r in self.artwork_refs
            ],
            "material": self.material,
            "channels": list(self.channels),
            "regions": list(self.regions),
            "quantity": self.quantity,
            "purpose": self.purpose.value,
            "promo_only": self.promo_only,
            "spec": self.spec,
        }
