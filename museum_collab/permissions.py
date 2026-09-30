"""信息隔离：合作方只见自身环节，合同正文不对普通运营开放。

环节（合作方登记时声明可参与哪些）：

- creative     创意/设计/打样
- production   生产
- distribution 铺货销售与退市善后

合作方账号只能访问自己的提案/工单，且每个字段按环节裁剪；
内部角色按职责取数。合同正文不存在于协作数据中，系统只固定哈希，
任何角色通过本系统取正文都会被拒绝（正文走受控库 body_ref）。
"""
from __future__ import annotations

from typing import Any

from .actors import Gate, Role
from .errors import AuthorizationError


class Stage:
    CREATIVE = "creative"
    PRODUCTION = "production"
    DISTRIBUTION = "distribution"
    ALL = (CREATIVE, PRODUCTION, DISTRIBUTION)


STAGE_LABELS = {
    Stage.CREATIVE: "创意设计",
    Stage.PRODUCTION: "生产",
    Stage.DISTRIBUTION: "销售退市",
}

# 哪些角色可以通过受控库取合同正文（法务流程在线下）。
# 普通运营人员明确不在其中。
CONTRACT_BODY_ROLES = frozenset({Role.ADMIN, Role.RIGHTS_OFFICER})


def assert_contract_body_allowed(role: str) -> None:
    if role not in CONTRACT_BODY_ROLES:
        raise AuthorizationError(
            "合同正文不向普通运营人员开放，仅可查看哈希与元数据"
        )


def contract_meta_view(contract: Any) -> dict[str, Any]:
    """任何列表/详情页只能看到这份无正文视图。"""
    return {
        "contract_id": contract.contract_id,
        "partner_id": contract.partner_id,
        "title": contract.title,
        "version": contract.version,
        "body_sha256": contract.body_sha256,
        "body_ref_kind": "sealed_storage",
        "body": None,
    }


def assert_partner_owns(actor: Any, partner_id: str) -> None:
    if actor.role != Role.PARTNER or actor.partner_id != partner_id:
        raise AuthorizationError("合作方只能访问属于本机构的流程数据")


def message_visible(audience: tuple[str, ...], actor: Any) -> bool:
    if actor.role in (Role.CURATOR, Role.ADMIN, Role.RIGHTS_OFFICER):
        return True
    if actor.role == Role.PARTNER:
        return actor.partner_id in audience
    return actor.role in audience


def _creative_section(version: Any) -> dict[str, Any]:
    c = version.content
    return {
        "stage": Stage.CREATIVE,
        "title": c.title,
        "version": c.version,
        "material": c.material,
        "element_uses": list(c.element_uses),
        "design_file_ref": c.design_file_ref,
        "design_file_sha256": c.design_file_sha256,
        "gates": {g: ("通过" if g in version.approvals else "待审")
                  for g in Gate.ALL},
    }


def _production_section(order: Any | None) -> dict[str, Any] | None:
    if order is None:
        return {"stage": Stage.PRODUCTION, "order": None}
    # 生产方只需按规格生产：给边界约束，不给权利人身份与审批内情
    sample_license = order.rights_snapshot.license_views[0] if order.rights_snapshot.license_views else {}
    scope = sample_license.get("scope", {})
    return {
        "stage": Stage.PRODUCTION,
        "order_id": order.order_id,
        "version_sha256": order.version_sha256,
        "material_note": "按批准版本哈希对应的冻结设计稿生产，不得替换图案",
        "quantity": order.quantity,
        "must_comply": {
            "regions": scope.get("regions"),
            "channels": scope.get("channels"),
            "valid_until": scope.get("ends_on"),
        },
        "frozen": order.frozen,
        "retired": order.retired,
    }


def _distribution_section(
    order: Any | None, instances: list[Any]
) -> dict[str, Any]:
    by_state: dict[str, int] = {}
    for inst in instances:
        by_state[inst.state] = by_state.get(inst.state, 0) + 1
    return {
        "stage": Stage.DISTRIBUTION,
        "order_id": order.order_id if order else None,
        "region": order.region if order else None,
        "channel": order.channel if order else None,
        "sellable": not order.frozen if order else False,
        "instances_by_state": by_state,
        "open_dispositions": [
            d for inst in instances for d in inst.disposition_ids
        ],
    }


def partner_briefing(
    *,
    actor: Any,
    partner: Any,
    version: Any,
    order: Any | None,
    instances: list[Any],
    visible_messages: list[dict[str, Any]],
) -> dict[str, Any]:
    """组装合作方在自己参与环节内可见的信息。"""
    assert_partner_owns(actor, partner.partner_id)
    sections: list[dict[str, Any]] = []
    if Stage.CREATIVE in partner.stages:
        sections.append(_creative_section(version))
    if Stage.PRODUCTION in partner.stages:
        sections.append(_production_section(order))
    if Stage.DISTRIBUTION in partner.stages:
        sections.append(_distribution_section(order, instances))
    return {
        "partner_id": partner.partner_id,
        "proposal_id": version.content.proposal_id,
        "visible_stages": list(partner.stages),
        "sections": sections,
        # 沟通内容只返回明确发送给该合作方的
        "messages": visible_messages,
    }
