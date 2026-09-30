"""许可：数量、地区、渠道、期限与权利类型。

馆藏入藏不等于授权。只有权利人通过许可明确授予
``reproduce``（复制）与 ``adapt_commercial``（商业改编），
相应元素才可用于商业文创。许可逐项表达：

- quantity   可生产/销售数量上限
- regions    授权地区
- channels   授权渠道（含仅展览宣传的非卖渠道）
- 有效期     starts_on / ends_on
- exclusive  是否独占（与竞争锁联动）
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# 渠道常量
class Channel:
    MUSEUM_SHOP = "museum_shop"        # 馆内零售
    ONLINE_STORE = "online_store"      # 线上商城
    OFFLINE_EVENT = "offline_event"    # 线下快闪/经销
    EXHIBITION_PROMO = "exhibition_promo"  # 仅展览宣传（非卖）
    SAMPLE = "sample"                  # 打样（非卖）

    COMMERCIAL = (MUSEUM_SHOP, ONLINE_STORE, OFFLINE_EVENT)
    ALL = COMMERCIAL + (EXHIBITION_PROMO, SAMPLE)


# 权利类型
class Right:
    REPRODUCE = "reproduce"
    ADAPT_COMMERCIAL = "adapt_commercial"
    PROMOTION = "promotion"


@dataclass(frozen=True)
class LicenseScope:
    quantity: int | None  # None 表示不限数量
    regions: frozenset[str]
    channels: frozenset[str]
    starts_on: str
    ends_on: str

    @staticmethod
    def _all_regions() -> frozenset[str]:
        return frozenset({"*"})

    def covers(
        self,
        *,
        quantity: int,
        region: str,
        channel: str,
        on_date: str,
        already_committed: int = 0,
    ) -> tuple[bool, str]:
        if not (self.starts_on <= on_date <= self.ends_on):
            return False, f"日期 {on_date} 超出许可期限 {self.starts_on}~{self.ends_on}"
        if "*" not in self.regions and region not in self.regions:
            return False, f"地区 {region} 不在许可范围 {sorted(self.regions)}"
        if channel not in self.channels:
            return False, f"渠道 {channel} 不在许可范围 {sorted(self.channels)}"
        if self.quantity is not None:
            if already_committed + quantity > self.quantity:
                return (
                    False,
                    f"数量不足：申请 {quantity} + 已承诺 {already_committed} "
                    f"> 许可上限 {self.quantity}",
                )
        return True, ""

    def to_data(self) -> dict[str, Any]:
        return {
            "quantity": self.quantity,
            "regions": sorted(self.regions),
            "channels": sorted(self.channels),
            "starts_on": self.starts_on,
            "ends_on": self.ends_on,
        }


@dataclass
class License:
    license_id: str
    artwork_id: str
    element_ids: frozenset[str]  # "*" 表示覆盖该作品全部已登记元素
    holder_id: str               # 授权的权利人
    partner_id: str              # 被许可的合作方
    rights: frozenset[str]
    scope: LicenseScope
    exclusive: bool = False
    status: str = "active"       # active / revoked
    revoked_reason: str = ""
    granted_entry_seq: int = 0
    revoked_entry_seq: int = 0

    def covers_element(self, artwork_id: str, element_id: str) -> bool:
        if self.artwork_id != artwork_id or self.status != "active":
            return False
        return "*" in self.element_ids or element_id in self.element_ids

    def covers_element_ever(self, artwork_id: str, element_id: str) -> bool:
        """与 covers_element 相同但不限状态：回溯已撤回许可用。"""
        if self.artwork_id != artwork_id:
            return False
        return "*" in self.element_ids or element_id in self.element_ids

    def boundary_view(self) -> dict[str, Any]:
        """当前权利边界的对外视图。"""
        return {
            "license_id": self.license_id,
            "artwork_id": self.artwork_id,
            "element_ids": sorted(self.element_ids),
            "partner_id": self.partner_id,
            "rights": sorted(self.rights),
            "scope": self.scope.to_data(),
            "exclusive": self.exclusive,
            "status": self.status,
            "revoked_reason": self.revoked_reason,
        }


def covers_use(
    licenses: list[License],
    *,
    artwork_id: str,
    element_id: str,
    partner_id: str,
    required_rights: set[str],
    quantity: int,
    region: str,
    channel: str,
    on_date: str,
    committed_by_license: dict[str, int],
) -> tuple[bool, str]:
    """是否存在一条有效许可覆盖某次具体使用。"""
    # 同一元素可能存在多条许可（例如新增渠道后补授权），
    # 任一条完整覆盖即可；范围不够的许可不能让搜索提前终止。
    last_reason = f"作品 {artwork_id} 元素 {element_id} 无有效许可"
    for lic in licenses:
        if lic.partner_id != partner_id:
            continue
        if not lic.covers_element(artwork_id, element_id):
            continue
        if not required_rights.issubset(lic.rights):
            last_reason = f"许可 {lic.license_id} 缺少权利 {sorted(required_rights - lic.rights)}"
            continue
        ok, reason = lic.scope.covers(
            quantity=quantity,
            region=region,
            channel=channel,
            on_date=on_date,
            already_committed=committed_by_license.get(lic.license_id, 0),
        )
        if ok:
            return True, lic.license_id
        last_reason = f"许可 {lic.license_id} {reason}"
    return False, last_reason
