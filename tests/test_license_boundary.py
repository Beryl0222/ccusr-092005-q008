"""许可四维（数量/地区/渠道/期限）+ 用途：放行量产时逐条校验。"""
from __future__ import annotations

import unittest

from museum_collab import (
    LicenseScope,
    Purpose,
    ReleaseBlocked,
)
from museum_collab.model import ReservationMode

from helpers import STAFF, approve_all_gates, build_system, partner, scarf_design


class LicenseBoundaryTest(unittest.TestCase):
    def setUp(self) -> None:
        self.sys = build_system()
        self.pid = "p-scarf"
        self.v1 = self.sys.create_proposal(
            partner("partner-a"), proposal_id=self.pid,
            exhibition_id="exh-2026-modern", partner_id="partner-a",
            design=scarf_design(),
        )
        approve_all_gates(self.sys, self.pid, self.v1)
        self.sys.reserve_elements(
            STAFF["rights"], proposal_id=self.pid, version_id=self.v1,
            mode=ReservationMode.PARALLEL_SAMPLE, valid_until="2027-12-31",
        )

    def release(self, **kw):
        defaults = dict(proposal_id=self.pid, version_id=self.v1,
                        quantity=100, channel="museum-shop", region="CN",
                        on_date="2026-09-01")
        defaults.update(kw)
        return self.sys.release_production(STAFF["business"], **defaults)

    def test_within_boundary_release_ok(self) -> None:
        self.release(quantity=500)
        self.release(quantity=1500)  # 累计 2000 正好等于上限

    def test_cumulative_quantity_cap_enforced(self) -> None:
        self.release(quantity=1800)
        with self.assertRaisesRegex(ReleaseBlocked, "许可边界"):
            self.release(quantity=201)  # 累计 2001 > 2000

    def test_unknown_channel_blocked(self) -> None:
        with self.assertRaisesRegex(ReleaseBlocked, "渠道"):
            self.release(channel="street-fair")

    def test_unknown_region_blocked(self) -> None:
        with self.assertRaisesRegex(ReleaseBlocked, "地区"):
            self.release(region="US")

    def test_term_window_enforced(self) -> None:
        with self.assertRaisesRegex(ReleaseBlocked, "许可边界"):
            self.release(on_date="2027-07-01")  # 许可 2027-06-30 到期
        with self.assertRaisesRegex(ReleaseBlocked, "许可边界"):
            self.release(on_date="2026-07-01")  # 尚未生效

    def test_online_mall_channel_covered_by_license(self) -> None:
        # 设计方案需要先包含该渠道（v1 只有 museum-shop），这里直接验证许可 covers 语义
        scope = LicenseScope(
            max_quantity=10, regions=frozenset({"CN"}),
            channels=frozenset({"museum-shop"}),
            valid_from="2026-01-01", valid_until="2026-12-31",
            purpose=Purpose.COMMERCIAL,
        )
        self.assertFalse(scope.covers(
            quantity=1, region="CN", channel="online-mall",
            on_date="2026-06-01", purpose=Purpose.COMMERCIAL))
        self.assertFalse(scope.covers(
            quantity=1, region="CN", channel="museum-shop",
            on_date="2026-06-01", purpose=Purpose.PUBLIC_BENEFIT))

    def test_license_grantor_must_be_registered_holder(self) -> None:
        pid = "p-other"
        v = self.sys.create_proposal(
            partner("partner-a"), proposal_id=pid,
            exhibition_id="exh-2026-modern", partner_id="partner-a",
            design=scarf_design(),
        )
        with self.assertRaisesRegex(Exception, "权利人名录"):
            self.sys.grant_license(
                STAFF["rights"], proposal_id=pid, version_id=v,
                holder_id="stranger-xx",
                scope=LicenseScope(
                    max_quantity=1, regions=frozenset({"CN"}),
                    channels=frozenset({"museum-shop"}),
                    valid_from="2026-01-01", valid_until="2027-01-01",
                    purpose=Purpose.COMMERCIAL),
                contract_ref="CT-bad", contract_body={"signed": False},
            )


if __name__ == "__main__":
    unittest.main()
