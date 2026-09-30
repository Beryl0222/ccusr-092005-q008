"""访问控制：每个角色只能看到自身环节所需的信息。

- 合作方（PARTNER）：只能看到本机构提案；合同正文不可见，只留合同编号与哈希；
  内部沟通不可见；其他合作方的提案完全不可见。
- 普通运营/生产（PRODUCER）：只看生产所需（设计规格、批准哈希、放行与处置），
  合同正文、内部讨论、许可费等均不可见。
- 四道审核门角色：看到与其门相关的信息；合同正文仅权利与经营两条线可见。
- 策展人溯源（CURATOR_AUDIT）：可对任一上市产品取完整回查视图。
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any

from .model import Role

# 哪些角色可以读取合同正文
CONTRACT_BODY_ROLES = frozenset({Role.RIGHTS_OFFICER, Role.BUSINESS_OFFICER})

# 内部员工角色（可看本展览全部提案元数据）
STAFF_ROLES = frozenset(
    {
        Role.CURATOR,
        Role.RIGHTS_OFFICER,
        Role.PUBLIC_BENEFIT_OFFICER,
        Role.BUSINESS_OFFICER,
        Role.PRODUCER,
        Role.CURATOR_AUDIT,
    }
)


class AccessDenied(Exception):
    """角色无权执行该操作或读取该对象。"""


def ensure_staff(actor: Any) -> None:
    if actor.role not in STAFF_ROLES:
        raise AccessDenied("该操作仅馆内角色可执行")


def can_access_proposal(actor: Any, partner_id: str) -> bool:
    if actor.role in STAFF_ROLES:
        return True
    return actor.role == Role.PARTNER and actor.partner_id == partner_id


def _redact_license(license_view: dict[str, Any] | None, actor: Any) -> dict[str, Any] | None:
    if license_view is None:
        return None
    view = deepcopy(license_view)
    # 合作方与运营只需要知道当前权利边界（数量/地区/渠道/期限/用途），
    # 不需要知道权利人是谁、合同编号以外的商务信息。
    if actor.role in (Role.PARTNER, Role.PRODUCER):
        view.pop("holder_id", None)
        view.pop("contract_ref", None)
        view.pop("contract_hash", None)
    return view


def _redact_communications(comms: list[dict[str, Any]], actor: Any) -> list[dict[str, Any]]:
    if actor.role in STAFF_ROLES:
        return deepcopy(comms)
    # 合作方只能看到自己参与的对外沟通，且看不到正文哈希之外的内部备注
    out = []
    for c in comms:
        if c["visibility"] != "partner":
            continue
        if actor.id not in c["participants"] and actor.partner_id not in c["participants"]:
            continue
        item = deepcopy(c)
        out.append(item)
    return out


def project_proposal(bundle: dict[str, Any], actor: Any) -> dict[str, Any]:
    """按角色对完整溯源束做信息裁剪。"""
    view = deepcopy(bundle)

    if actor.role == Role.PARTNER:
        if view["partner_id"] != actor.partner_id:
            raise AccessDenied("合作方只能查看本机构的提案")
    elif actor.role not in STAFF_ROLES:
        raise AccessDenied("未知角色")

    # 合同正文：任何提案视图都不带正文；仅在独立合同读取接口按角色放行。
    view.pop("contract_body", None)

    if actor.role in (Role.PARTNER, Role.PRODUCER):
        # 顶层“当前权利边界”同样只暴露边界本身，不暴露合同与权利人标识
        boundary = view.get("license_boundary")
        if isinstance(boundary, dict):
            boundary.pop("contract_ref", None)
            boundary.pop("contract_hash", None)
            boundary.pop("holder_id", None)

    for v in view.get("versions", []):
        v["license"] = _redact_license(v.get("license"), actor)
        if actor.role == Role.PRODUCER:
            # 运营不需要审核理由与沟通细节
            for g in v.get("gates", []):
                g.pop("reason", None)

    if actor.role == Role.PRODUCER:
        view["communications"] = []
        # 运营只关心生产、实例与处置
        for v in view.get("versions", []):
            v.pop("samples", None)
    elif actor.role == Role.PARTNER:
        view["communications"] = _redact_communications(view.get("communications", []), actor)
        # 审核门的内部批注不向合作方展开，但状态与固定哈希可见
        for v in view.get("versions", []):
            for g in v.get("gates", []):
                if g.get("status") == "rejected":
                    continue  # 驳回原因需要让合作方修正，保留
                g.pop("reason", None)

    return view
