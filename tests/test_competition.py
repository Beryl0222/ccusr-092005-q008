"""同一艺术元素的竞争：并行打样放行、排他占用阻断、先量产者得。"""
from __future__ import annotations

import unittest

from museum_collab import ChangeFacet, ElementConflict, Gate
from museum_collab.model import ReservationMode

from helpers import STAFF, approve_all_gates, build_system, partner, scarf_design, wave_design


def fully_approve(sys, pid, partner_id, design, contract_ref=None):
    v = sys.create_proposal(
        partner(partner_id), proposal_id=pid,
        exhibition_id="exh-2026-modern",
        partner_id=partner_id,
        design=design,
    )
    ref = contract_ref or f"CT-{pid}-001"
    approve_all_gates(sys, pid, v)
    return v


class ElementCompetitionTest(unittest.TestCase):
    def setUp(self) -> None:
        self.sys = build_system()
        # 两个合作方、两个提案，使用同一元素 main-figure
        self.va = fully_approve(self.sys, "p-a", "partner-a", scarf_design())
        self.vb = fully_approve(self.sys, "p-b", "partner-b", scarf_design())

    def test_parallel_sampling_both_allowed(self) -> None:
        self.sys.reserve_elements(
            STAFF["rights"], proposal_id="p-a", version_id=self.va,
            mode=ReservationMode.PARALLEL_SAMPLE, valid_until="2026-12-31",
        )
        self.sys.reserve_elements(
            STAFF["rights"], proposal_id="p-b", version_id=self.vb,
            mode=ReservationMode.PARALLEL_SAMPLE, valid_until="2026-12-31",
        )
        self.sys.start_sampling(partner("partner-a"), proposal_id="p-a",
                                version_id=self.va, quantity=3)
        self.sys.start_sampling(partner("partner-b"), proposal_id="p-b",
                                version_id=self.vb, quantity=3)

    def test_exclusive_option_blocks_second_partner(self) -> None:
        self.sys.reserve_elements(
            STAFF["rights"], proposal_id="p-a", version_id=self.va,
            mode=ReservationMode.EXCLUSIVE_OPTION, valid_until="2026-12-31",
        )
        with self.assertRaisesRegex(ElementConflict, "排他占用"):
            self.sys.reserve_elements(
                STAFF["rights"], proposal_id="p-b", version_id=self.vb,
                mode=ReservationMode.PARALLEL_SAMPLE, valid_until="2026-12-31",
            )

    def test_exclusive_cannot_be_granted_after_parallel_started(self) -> None:
        self.sys.reserve_elements(
            STAFF["rights"], proposal_id="p-a", version_id=self.va,
            mode=ReservationMode.PARALLEL_SAMPLE, valid_until="2026-12-31",
        )
        with self.assertRaisesRegex(ElementConflict, "并行占用"):
            self.sys.reserve_elements(
                STAFF["rights"], proposal_id="p-b", version_id=self.vb,
                mode=ReservationMode.EXCLUSIVE_OPTION, valid_until="2026-12-31",
            )

    def test_first_to_production_wins_element(self) -> None:
        # 并行打样 → 甲先量产 → 乙量产同一元素被阻断
        for pid, v in (("p-a", self.va), ("p-b", self.vb)):
            self.sys.reserve_elements(
                STAFF["rights"], proposal_id=pid, version_id=v,
                mode=ReservationMode.PARALLEL_SAMPLE, valid_until="2027-12-31",
            )
        self.sys.release_production(
            STAFF["business"], proposal_id="p-a", version_id=self.va,
            quantity=100, channel="museum-shop", region="CN", on_date="2026-09-01",
        )
        with self.assertRaisesRegex(ElementConflict, "量产"):
            self.sys.release_production(
                STAFF["business"], proposal_id="p-b", version_id=self.vb,
                quantity=100, channel="museum-shop", region="CN", on_date="2026-09-02",
            )

    def test_same_partner_revision_keeps_access_to_element(self) -> None:
        # 甲自己量产过的元素，甲的新版本仍可继续使用
        self.sys.release_production(
            STAFF["business"], proposal_id="p-a", version_id=self.va,
            quantity=100, channel="museum-shop", region="CN", on_date="2026-09-01",
        )
        vid = self.sys.revise_proposal(
            partner("partner-a"), proposal_id="p-a",
            facets=[ChangeFacet.QUANTITY],
            design=scarf_design(quantity=800),
        )
        # 数量变化只过经营门；其他三门沿用，许可沿用
        self.sys.decide_gate(
            STAFF["business"], proposal_id="p-a", version_id=vid,
            gate=Gate.BUSINESS, approved=True, reason="追加数量在许可上限内",
        )
        self.sys.release_production(
            STAFF["business"], proposal_id="p-a", version_id=vid,
            quantity=400, channel="museum-shop", region="CN", on_date="2026-09-10",
        )

    def test_different_element_not_blocked(self) -> None:
        # 乙改用另一个元素（水纹），与甲的主山体竞争无关
        sys = build_system()
        va = fully_approve(sys, "p-a", "partner-a", scarf_design())
        vb = fully_approve(sys, "p-c", "partner-b", wave_design())
        sys.reserve_elements(
            STAFF["rights"], proposal_id="p-a", version_id=va,
            mode=ReservationMode.EXCLUSIVE_OPTION, valid_until="2026-12-31",
        )
        # 不同元素：乙的水纹排他意向照样放行
        sys.reserve_elements(
            STAFF["rights"], proposal_id="p-c", version_id=vb,
            mode=ReservationMode.EXCLUSIVE_OPTION, valid_until="2026-12-31",
        )


if __name__ == "__main__":
    unittest.main()
