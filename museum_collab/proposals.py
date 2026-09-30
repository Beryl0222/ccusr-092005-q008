"""设计提案、版本与四类审批门。

关键规则：

1. 提案必须逐元素标明取用哪件作品的哪个元素，引用即参与版本哈希，
   杜绝"群里口头说一声"式的无记录授权。
2. 每次修订生成新版本；系统按变更内容计算影响面，
   只失效并要求重开真正受影响的审批门——
   换销售渠道不必重做学术审核，改纹样则学术与权利要重来。
3. 批准内容用哈希固定：审批决定钉住被批准版本的哈希与其前驱版本，
   生产只能引用"全门通过且未失效"的版本。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .actors import GATE_LABELS, Gate
from .errors import ApprovalRequiredError, ValidationError

# 变更类别
class Change:
    ELEMENTS = "elements"      # 取用元素变化（换图案/换作品）
    ADAPTATION = "adaptation"  # 改编方式变化（同元素，改用法）
    MATERIAL = "material"      # 材质变化
    CHANNEL = "channel"        # 销售渠道变化
    REGION = "region"          # 地区变化
    QUANTITY = "quantity"      # 数量变化
    PRICE = "price"            # 定价（只影响经营，公益属性另看规则）
    PUBLIC_BENEFIT_USE = "public_benefit_use"  # 公益用途/收益安排变化
    TYPO = "typo"              # 纯文案订正，不触发任何门

    ALL = (ELEMENTS, ADAPTATION, MATERIAL, CHANNEL, REGION,
           QUANTITY, PRICE, PUBLIC_BENEFIT_USE, TYPO)


# 变更类别 -> 必须重开的审批门
CHANGE_IMPACT: dict[str, frozenset[str]] = {
    # 换图案：学术要重看是否忠于原作，权利要重新确认覆盖
    Change.ELEMENTS: frozenset({Gate.ACADEMIC, Gate.RIGHTS, Gate.BUSINESS}),
    # 改编方式变化：学术与权利关心；经营门视渠道不变则保留
    Change.ADAPTATION: frozenset({Gate.ACADEMIC, Gate.RIGHTS}),
    # 材质变化：经营（成本/品质）与学术（呈现是否走样）判断
    Change.MATERIAL: frozenset({Gate.ACADEMIC, Gate.BUSINESS}),
    # 渠道变化：权利（许可渠道范围）与经营
    Change.CHANNEL: frozenset({Gate.RIGHTS, Gate.BUSINESS}),
    # 地区变化：只需权利确认许可地区覆盖
    Change.REGION: frozenset({Gate.RIGHTS}),
    # 数量变化：只需权利确认许可数量余量；经营对铺货量知情，不阻断
    Change.QUANTITY: frozenset({Gate.RIGHTS}),
    # 定价：仅经营
    Change.PRICE: frozenset({Gate.BUSINESS}),
    # 公益收益安排变化：公益判断 + 经营
    Change.PUBLIC_BENEFIT_USE: frozenset({
        Gate.PUBLIC_BENEFIT, Gate.BUSINESS
    }),
    # 纯文案订正：不重开任何门（仍生成新版本留痕）
    Change.TYPO: frozenset(),
}


@dataclass(frozen=True)
class ProposalContent:
    """某一版本的完整设计内容（参与哈希的全部字段）。"""

    proposal_id: str
    version: int
    partner_id: str
    exhibition_id: str
    title: str
    # 元素取用：[{"artwork_id","element_id","adaptation"}]
    element_uses: tuple[dict[str, Any], ...]
    material: str
    regions: tuple[str, ...]
    channels: tuple[str, ...]
    quantity: int
    unit_price: int
    public_benefit: bool          # 是否主张公益属性
    benefit_statement: str        # 公益收益安排说明
    design_file_ref: str          # 设计稿在受控存储的引用
    design_file_sha256: str       # 设计稿哈希
    based_on_version: int | None  # 前驱版本
    notes: str = ""

    def to_hash_payload(self) -> dict[str, Any]:
        return {
            "proposal_id": self.proposal_id,
            "version": self.version,
            "partner_id": self.partner_id,
            "exhibition_id": self.exhibition_id,
            "title": self.title,
            "element_uses": list(self.element_uses),
            "material": self.material,
            "regions": list(self.regions),
            "channels": list(self.channels),
            "quantity": self.quantity,
            "unit_price": self.unit_price,
            "public_benefit": self.public_benefit,
            "benefit_statement": self.benefit_statement,
            "design_file_ref": self.design_file_ref,
            "design_file_sha256": self.design_file_sha256,
            "based_on_version": self.based_on_version,
            "notes": self.notes,
        }


@dataclass
class ApprovalRecord:
    gate: str
    version: int
    content_sha256: str
    decider_actor_id: str
    note: str
    entry_seq: int
    # 审批时钉住的权利边界快照（权利门填写），供回溯"当时依据什么批的"
    boundary_sha256: str = ""
    # 修订后未经重审而沿用的旧批准：记录来源版本
    carried_from: int | None = None


@dataclass
class ProposalVersion:
    content: ProposalContent
    content_sha256: str
    changes_from_prev: tuple[str, ...]
    invalidated_at_revision: int | None = None  # 在哪个新版本后失效
    approvals: dict[str, ApprovalRecord] = field(default_factory=dict)

    def live_gates(self) -> set[str]:
        return set(self.approvals.keys())

    def is_fully_approved(self) -> bool:
        return set(Gate.ALL).issubset(self.approvals.keys())


def diff_versions(
    prev: ProposalContent, new: ProposalContent
) -> list[str]:
    """比较两版内容，返回变更类别列表（影响面分析的输入）。"""
    changes: list[str] = []
    if (
        [(u["artwork_id"], u["element_id"]) for u in prev.element_uses]
        != [(u["artwork_id"], u["element_id"]) for u in new.element_uses]
    ):
        changes.append(Change.ELEMENTS)
    elif [
        (u["artwork_id"], u["element_id"], u.get("adaptation"))
        for u in prev.element_uses
    ] != [
        (u["artwork_id"], u["element_id"], u.get("adaptation"))
        for u in new.element_uses
    ]:
        changes.append(Change.ADAPTATION)
    if prev.design_file_sha256 != new.design_file_sha256 and Change.ELEMENTS not in changes:
        # 设计稿文件变了但元素清单没变，按改编方式变化处理
        changes.append(Change.ADAPTATION)
    if prev.material != new.material:
        changes.append(Change.MATERIAL)
    if set(prev.channels) != set(new.channels):
        changes.append(Change.CHANNEL)
    if set(prev.regions) != set(new.regions):
        changes.append(Change.REGION)
    if prev.quantity != new.quantity:
        changes.append(Change.QUANTITY)
    if prev.unit_price != new.unit_price:
        changes.append(Change.PRICE)
    if (
        prev.public_benefit != new.public_benefit
        or prev.benefit_statement != new.benefit_statement
    ):
        changes.append(Change.PUBLIC_BENEFIT_USE)
    if not changes and prev.notes != new.notes:
        changes.append(Change.TYPO)
    return changes


def impacted_gates(changes: list[str]) -> frozenset[str]:
    gates: set[str] = set()
    for change in changes:
        gates |= CHANGE_IMPACT[change]
    return frozenset(gates)


@dataclass
class Proposal:
    proposal_id: str
    versions: dict[int, ProposalVersion] = field(default_factory=dict)
    current_version: int = 0
    created_entry_seq: int = 0

    def latest(self) -> ProposalVersion:
        return self.versions[self.current_version]

    def approved_version_for_production(self) -> ProposalVersion | None:
        """返回可用于生产的版本：当前版本且四门全通。

        历史版本即便全通过也不能用于新生产——修订一旦发生，
        旧版本即标记 invalidated_at_revision，防止错误版本流入产线。
        """
        v = self.latest()
        if v.invalidated_at_revision is not None:
            return None
        if v.is_fully_approved():
            return v
        return None

    def missing_gates(self) -> list[str]:
        v = self.latest()
        return [g for g in Gate.ALL if g not in v.approvals]

    def assert_production_ready(self) -> ProposalVersion:
        v = self.latest()
        if v.invalidated_at_revision is not None:
            raise ApprovalRequiredError(
                f"提案 {self.proposal_id} 当前版本已被后续修订取代",
                missing=list(Gate.ALL),
            )
        missing = self.missing_gates()
        if missing:
            labels = "、".join(GATE_LABELS[g] for g in missing)
            raise ApprovalRequiredError(
                f"提案 {self.proposal_id} 尚缺审批：{labels}",
                missing=missing,
            )
        return v
