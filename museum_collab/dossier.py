"""策展人回溯档案：拿到任一上市产品，回查全链路。

回溯内容（均来自只追加台账与状态，不做删除）：

1. 原作：作品档案、取用元素
2. 沟通过程：全部结构化消息（含内容哈希）
3. 批准版本：每个版本四门批准记录、版本哈希与前驱关系
4. 当前权利边界：覆盖许可的最新状态（含撤回记录）
5. 生产与实例：工单、哈希钉住关系、实例状态变迁
6. 事件与处置：撤回/改期及逐实例处置
"""
from __future__ import annotations

from typing import Any


def build_dossier(platform: Any, *, instance_id: str) -> dict[str, Any]:
    inst = platform.instances[instance_id]
    proposal = platform.proposals[inst.proposal_id]
    order = platform.orders[inst.order_id]

    artwork_ids = sorted({
        u["artwork_id"]
        for v in proposal.versions.values()
        for u in v.content.element_uses
    })

    version_trace = []
    for vn in sorted(proposal.versions):
        v = proposal.versions[vn]
        version_trace.append({
            "version": vn,
            "content_sha256": v.content_sha256,
            "based_on_version": v.content.based_on_version,
            "changes_from_prev": list(v.changes_from_prev),
            "invalidated_at_revision": v.invalidated_at_revision,
            "approvals": [
                {
                    "gate": rec.gate,
                    "decider": rec.decider_actor_id,
                    "on_version": rec.version,
                    "carried_from": rec.carried_from,
                    "boundary_sha256": rec.boundary_sha256,
                    "entry_seq": rec.entry_seq,
                    "note": rec.note,
                }
                for rec in v.approvals.values()
            ],
        })

    messages = platform.messages_for(proposal.proposal_id)
    license_views = platform.current_boundary_for(proposal.proposal_id)
    dispositions = platform.dispositions_for_instance(instance_id)

    return {
        "instance_id": instance_id,
        "instance_state": inst.state,
        "instance_history": list(inst.history),
        "product": {
            "proposal_id": proposal.proposal_id,
            "current_version": proposal.current_version,
            "produced_version": inst.version,
            "version_sha256_pinned_by_order": order.version_sha256,
        },
        "artworks": [
            platform.artwork_view(aid) for aid in artwork_ids
        ],
        "communication": [
            {
                "message_id": m.message_id,
                "author": m.author_actor_id,
                "audience": list(m.audience),
                "content": m.content,
                "content_digest": m.digest_payload(),
                "timestamp": m.timestamp,
            }
            for m in messages
        ],
        "version_trace": version_trace,
        "rights_boundary": license_views,
        "production_order": {
            "order_id": order.order_id,
            "license_ids": list(order.license_ids),
            "contract_sha256": order.contract_sha256,
            "rights_snapshot_sha256": order.rights_snapshot.snapshot_sha256,
            "rights_snapshot": list(order.rights_snapshot.license_views),
            "created_on": order.created_on,
            "frozen": order.frozen,
            "retired": order.retired,
        },
        "dispositions": [
            {
                "disposition_id": d.disposition_id,
                "trigger": d.trigger,
                "state_at_event": d.state_at_event,
                "action": d.action,
                "rationale": d.rationale,
                "issued_on": d.issued_on,
                "context": d.context,
            }
            for d in dispositions
        ],
    }
