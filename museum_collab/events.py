"""权利人撤回与开幕改期的分类处置。

核心原则：事件不"一刀切"改写历史，而是对每一个实例按其当前状态
生成独立处置，旧的批准与生产记录原样保留、可回溯。

事件 × 实例状态处置矩阵：

权利人撤回许可
- unproduced        取消投产（不得开工）
- in_transit        暂停销售、召回/封存
- sold              不溯及追回；停售、消费者告知与结算/补偿
- exhibition_promo  按期撤换宣传物料

开幕改期
- unproduced        暂缓投产；若新档期落在许可期限外则升级续权，不放行
- in_transit        暂缓铺货、就地等待新档期
- sold              不处理（交易在改期前已完成），留档
- exhibition_promo  更新档期信息、订正已发布物料
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .production import InstanceState


class Trigger:
    LICENSE_REVOKED = "license_revoked"
    SCHEDULE_CHANGED = "schedule_changed"


class Action:
    # 撤回
    CANCEL_PRODUCTION = "cancel_production"
    RECALL_AND_HOLD = "recall_and_hold"
    POST_SALE_NOTICE = "post_sale_notice"
    TAKE_DOWN_PROMO = "take_down_promo"
    # 改期
    HOLD_PRODUCTION = "hold_production"
    ESCALATE_RIGHTS_RENEWAL = "escalate_rights_renewal"
    HOLD_DISTRIBUTION = "hold_distribution"
    KEEP_SOLD_RECORD = "keep_sold_record"
    UPDATE_PROMO_DATES = "update_promo_dates"


@dataclass(frozen=True)
class Disposition:
    disposition_id: str
    trigger: str
    instance_id: str
    order_id: str
    state_at_event: str
    action: str
    rationale: str
    issued_on: str
    entry_seq: int
    context: dict[str, Any]
    # 处置不覆盖任何旧记录，只追加；这里留空表示纯新增
    supersedes_disposition_id: str | None = None


def plan_for_instance(
    trigger: str,
    state: str,
    *,
    license_covers_new_window: bool | None = None,
) -> tuple[str, str]:
    """返回 (动作, 理由)。纯函数，便于单测逐格验证矩阵。"""
    if trigger == Trigger.LICENSE_REVOKED:
        return {
            InstanceState.UNPRODUCED: (
                Action.CANCEL_PRODUCTION,
                "未生产实例：许可已撤回，立即取消投产，防止错误版本流入生产",
            ),
            InstanceState.IN_TRANSIT: (
                Action.RECALL_AND_HOLD,
                "在途实例：暂停一切销售，就地封存或召回，按权利善后方案处理",
            ),
            InstanceState.SOLD: (
                Action.POST_SALE_NOTICE,
                "已售实例：交易在撤回前完成，不溯及追回；停止继续销售，"
                "留存消费者告知与收益结算/补偿安排",
            ),
            InstanceState.EXHIBITION_PROMO: (
                Action.TAKE_DOWN_PROMO,
                "展览宣传实例：宣传使用随撤回终止，按期撤换展陈与传播物料",
            ),
        }[state]

    if trigger == Trigger.SCHEDULE_CHANGED:
        if state == InstanceState.UNPRODUCED:
            if license_covers_new_window is False:
                return (
                    Action.ESCALATE_RIGHTS_RENEWAL,
                    "未生产实例：新开幕档期超出当前许可期限，暂缓投产并"
                    "升级办理续权，续权完成前不得放行",
                )
            return (
                Action.HOLD_PRODUCTION,
                "未生产实例：暂缓投产，按新开幕档期排产，许可仍覆盖新档期",
            )
        if state == InstanceState.IN_TRANSIT:
            return (
                Action.HOLD_DISTRIBUTION,
                "在途实例：暂缓铺货与上架，就地等待新档期，避免提前上市",
            )
        if state == InstanceState.SOLD:
            return (
                Action.KEEP_SOLD_RECORD,
                "已售实例：交易在改期前完成，不受档期影响，原记录保留",
            )
        if state == InstanceState.EXHIBITION_PROMO:
            return (
                Action.UPDATE_PROMO_DATES,
                "展览宣传实例：更新物料档期信息，订正已发布内容，不销毁物料",
            )

    raise ValueError(f"未知事件/状态组合: {trigger} / {state}")
