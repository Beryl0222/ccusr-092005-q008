"""创意立项与四道审核门：角色分立、驳回、未授权不得打样。"""
from __future__ import annotations

import unittest

from museum_collab import (
    AccessDenied,
    ChangeFacet,
    DesignContent,
    ElementRef,
    Gate,
    Purpose,
    RefMode,
    ReleaseBlocked,
)
from museum_collab.model import ReservationMode

from helpers import STAFF, approve_all_gates, build_system, partner, scarf_design


class CreativeReviewTest(unittest.TestCase):
    def setUp(self) -> None:
        self.sys = build_system()
        self.pid = "p-scarf"
        self.v1 = self.sys.create_proposal(
            partner("partner-a"), proposal_id=self.pid,
            exhibition_id="exh-2026-modern", partner_id="partner-a",
            design=scarf_design(),
        )

    def test_proposal_must_name_artwork_and_elements(self) -> None:
        with self.assertRaisesRegex(Exception, "必须标明"):
            self.sys.create_proposal(
                partner("partner-a"), proposal_id="p-empty",
                exhibition_id="exh-2026-modern", partner_id="partner-a",
                design=DesignContent(
                    artwork_refs=(), material="silk", channels=("museum-shop",),
                    regions=("CN",), quantity=10, purpose=Purpose.COMMERCIAL,
                ),
            )

    def test_unknown_artwork_or_element_is_rejected(self) -> None:
        bad = scarf_design(artwork_refs=(ElementRef(
            "artwork-river-17", ("ghost-element",), RefMode.REPRODUCTION),))
        with self.assertRaisesRegex(Exception, "不存在元素"):
            self.sys.create_proposal(
                partner("partner-a"), proposal_id="p-bad",
                exhibition_id="exh-2026-modern", partner_id="partner-a",
                design=bad,
            )

    def test_each_gate_only_its_role(self) -> None:
        # 合作方不能审批任何门
        with self.assertRaises(AccessDenied):
            self.sys.decide_gate(
                partner("partner-a"), proposal_id=self.pid, version_id=self.v1,
                gate=Gate.ACADEMIC, approved=True,
            )
        # 策展人不能越权做权利确认
        with self.assertRaises(AccessDenied):
            self.sys.decide_gate(
                STAFF["curator"], proposal_id=self.pid, version_id=self.v1,
                gate=Gate.RIGHTS, approved=True,
            )

    def test_rights_gate_blocks_without_license(self) -> None:
        self.sys.decide_gate(
            STAFF["curator"], proposal_id=self.pid, version_id=self.v1,
            gate=Gate.ACADEMIC, approved=True,
        )
        with self.assertRaisesRegex(ReleaseBlocked, "生效许可"):
            self.sys.decide_gate(
                STAFF["rights"], proposal_id=self.pid, version_id=self.v1,
                gate=Gate.RIGHTS, approved=True,
            )

    def test_sampling_before_rights_clearance_is_blocked(self) -> None:
        """事故复现点：仅有学术通过、权利未确认时，打样被系统阻断。"""
        self.sys.decide_gate(
            STAFF["curator"], proposal_id=self.pid, version_id=self.v1,
            gate=Gate.ACADEMIC, approved=True,
        )
        with self.assertRaisesRegex(ReleaseBlocked, "rights"):
            self.sys.start_sampling(
                partner("partner-a"), proposal_id=self.pid,
                version_id=self.v1, quantity=3,
            )

    def test_rejection_keeps_version_unapproved_and_blocks_release(self) -> None:
        approve_all_gates(self.sys, self.pid, self.v1)
        # 学术在复核中驳回当前版本
        self.sys.revise_proposal(
            partner("partner-a"), proposal_id=self.pid,
            facets=[ChangeFacet.ADAPTATION], design=scarf_design(
                spec={"pattern": "放大主山体 1.4 倍"}),
        )
        v2 = self.sys._proposal(self.pid).current.version_id
        self.sys.decide_gate(
            STAFF["curator"], proposal_id=self.pid, version_id=v2,
            gate=Gate.ACADEMIC, approved=False, reason="改编过度，偏离原作构图",
        )
        with self.assertRaisesRegex(ReleaseBlocked, "未全部通过"):
            self.sys.release_production(
                STAFF["business"], proposal_id=self.pid, version_id=v2,
                quantity=100, channel="museum-shop", region="CN",
            )

    def test_full_approval_then_release(self) -> None:
        approve_all_gates(self.sys, self.pid, self.v1)
        self.sys.reserve_elements(
            STAFF["rights"], proposal_id=self.pid, version_id=self.v1,
            mode=ReservationMode.PARALLEL_SAMPLE, valid_until="2026-12-31",
        )
        self.sys.start_sampling(partner("partner-a"), proposal_id=self.pid,
                                version_id=self.v1, quantity=5)
        self.sys.release_production(
            STAFF["business"], proposal_id=self.pid, version_id=self.v1,
            quantity=500, channel="museum-shop", region="CN",
        )
        view = self.sys.view_proposal(STAFF["business"], self.pid)
        self.assertEqual(view["stage"], "approved")
        self.assertEqual(view["versions"][0]["releases"][0]["quantity"], 500)

    def test_exhibition_promo_needs_no_commercial_license(self) -> None:
        pid = "p-poster"
        design = scarf_design(purpose=Purpose.EXHIBITION_PROMO, quantity=20)
        v = self.sys.create_proposal(
            partner("partner-a"), proposal_id=pid,
            exhibition_id="exh-2026-modern", partner_id="partner-a", design=design,
        )
        for actor, gate, reason in (
            (STAFF["curator"], Gate.ACADEMIC, "展宣海报"),
            (STAFF["rights"], Gate.RIGHTS, "入藏已含展览宣传授权"),
            (STAFF["benefit"], Gate.BENEFIT, "非营利展宣"),
            (STAFF["business"], Gate.BUSINESS, "不对外销售"),
        ):
            self.sys.decide_gate(actor, proposal_id=pid, version_id=v,
                                 gate=gate, approved=True, reason=reason)
        # 无许可也可放行（展宣用途）
        self.sys.release_production(
            STAFF["producer"], proposal_id=pid, version_id=v,
            quantity=20, channel="museum-shop", region="CN",
        )


if __name__ == "__main__":
    unittest.main()
