"""角色与四类审批门。

需求中明确拆分的四种判断，分别由对应角色完成，
彼此不替代、不合并：

- 学术审核 ACADEMIC    元素取用是否忠于原作、学术上成立
- 权利确认 RIGHTS      复制与商业改编是否取得权利人许可
- 公益属性判断 PUBLIC_BENEFIT  是否符合公益/免税销售属性要求
- 经营审批 BUSINESS    材质、渠道、定价、库存等经营决策
"""
from __future__ import annotations

from dataclasses import dataclass


class Role:
    CURATOR = "curator"          # 策展人（创意发起、回溯）
    DESIGNER = "designer"        # 设计师（提案）
    ACADEMIC = "academic_reviewer"
    RIGHTS_OFFICER = "rights_officer"
    PUBLIC_BENEFIT_OFFICER = "public_benefit_officer"
    BUSINESS = "business_reviewer"
    RIGHTS_HOLDER = "rights_holder"
    OPERATIONS = "operations"    # 普通运营人员
    PARTNER = "partner"          # 合作方账号（绑定具体合作方）
    ADMIN = "admin"              # 档案管理员

    # 审批门 -> 唯一有权批准的角色
    GATE_OWNERS: dict[str, str] = {}


class Gate:
    ACADEMIC = "academic"
    RIGHTS = "rights"
    PUBLIC_BENEFIT = "public_benefit"
    BUSINESS = "business"

    ALL = (ACADEMIC, RIGHTS, PUBLIC_BENEFIT, BUSINESS)


Role.GATE_OWNERS = {
    Gate.ACADEMIC: Role.ACADEMIC,
    Gate.RIGHTS: Role.RIGHTS_OFFICER,
    Gate.PUBLIC_BENEFIT: Role.PUBLIC_BENEFIT_OFFICER,
    Gate.BUSINESS: Role.BUSINESS,
}

GATE_LABELS = {
    Gate.ACADEMIC: "学术审核",
    Gate.RIGHTS: "权利确认",
    Gate.PUBLIC_BENEFIT: "公益属性判断",
    Gate.BUSINESS: "经营审批",
}


@dataclass(frozen=True)
class Actor:
    """系统中的行动者。合作方账号必须绑定 partner_id。"""

    actor_id: str
    role: str
    partner_id: str | None = None
    name: str = ""

    def __post_init__(self) -> None:
        if self.role == Role.PARTNER and not self.partner_id:
            raise ValueError("合作方账号必须绑定 partner_id")
        if self.role != Role.PARTNER and self.partner_id is not None:
            raise ValueError("仅合作方账号可以绑定 partner_id")
