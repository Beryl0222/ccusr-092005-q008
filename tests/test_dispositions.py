"""权利人撤回与开幕改期：按实例状态分别处置，绝不一刀切。"""
from __future__ import annotations

import unittest

from museum_collab import DispositionAction, Trigger
from museum_collab.model import ReservationMode

from helpers import STAFF, approve_all_gates, build_system, partner, scarf_design


def actions(result: list[dict], trigger: Trigger) -> dict[str, str]:
    return {d["state"]: d["action"] for d in result if d["trigger"] == trigger.value}


class DispositionTest(unittest.TestCase):
    def _approved_proposal(self, pid: str = "p-scarf", license_until: str = "2027-06-30"):
        sys = build_system()
        v = sys.create_proposal(
            partner("partner-a"), proposal_id=pid,
            exhibition_id="exh-2026-modern", partner_id="partner-a",
            design=scarf_design(),
        )
        approve_all_gates(sys, pid, v, standard_license_until(license_until))
        sys.reserve_elements(
            STAFF["rights"], proposal_id=pid, version_id=v,
            mode=ReservationMode.PARALLEL_SAMPLE, valid_until="2027-12-31",
        )
        sys.release_production(
            STAFF["business"], proposal_id=pid, version_id=v,
            quantity=300, channel="museum-shop", region="CN", on_date="2026-09-01",
        )
        return sys, v

    def test_rights_withdrawal_four_instance_states(self) -> None:
        sys, v = self._approved_proposal()
        sys.register_instances(
            STAFF["producer"], proposal_id="p-scarf",
            instances=[
                {"instance_id": "i-1", "state": "not_produced", "quantity": 200},
                {"instance_id": "i-2", "state": "in_transit", "quantity": 100},
                {"instance_id": "i-3", "state": "sold", "detail": "已售 80 件"},
                {"instance_id": "i-4", "state": "promo_only", "quantity": 6},
            ],
        )
        result = sys.rights_withdrawn(
            STAFF["rights"], proposal_id="p-scarf",
            holder_id="artist-chen", effective_on="2026-09-15",
        )
        by_state = actions(result, Trigger.RIGHTS_WITHDRAWN)
        self.assertEqual(
            by_state,
            {
                "not_produced": DispositionAction.CANCEL_BEFORE_PRODUCTION.value,
                "in_transit": DispositionAction.RECALL_OR_HOLD.value,
                "sold": DispositionAction.HONOR_SOLD_COPIES.value,
                "promo_only": DispositionAction.PROMO_HALT.value,
            },
        )
        # 提案冻结，仍在审批中的错误版本不能再投产
        view = sys.view_proposal(STAFF["business"], "p-scarf")
        current = view["versions"][-1]
        self.assertEqual(view["stage"], "suspended")
        from museum_collab import ReleaseBlocked
        with self.assertRaisesRegex(ReleaseBlocked, "撤回"):
            sys.release_production(
                STAFF["business"], proposal_id="p-scarf", version_id=v,
                quantity=10, channel="museum-shop", region="CN",
            )

    def test_withdrawal_does_not_erase_history(self) -> None:
        sys, v = self._approved_proposal()
        sys.register_instances(
            STAFF["producer"], proposal_id="p-scarf",
            instances=[{"instance_id": "i-1", "state": "not_produced", "quantity": 1}],
        )
        sys.rights_withdrawn(
            STAFF["rights"], proposal_id="p-scarf",
            holder_id="artist-chen", effective_on="2026-09-15",
        )
        # 旧批准、旧放行、旧许可的历史都还在
        proposal = sys._proposal("p-scarf")
        self.assertEqual(proposal.versions[0].gates["rights"].status, "approved")
        self.assertEqual(proposal.versions[0].releases[0]["quantity"], 300)
        self.assertEqual(proposal.versions[0].license["status"], "withdrawn")
        # 已生成的处置记录只有一条且不可变
        self.assertEqual(len(proposal.dispositions), 1)

    def test_opening_rescheduled_beyond_license_term_blocks_further_release(self) -> None:
        """开幕推迟到许可到期之后：展宣品须复核，未生产/在途暂停而非销毁。"""
        sys, v = self._approved_proposal(license_until="2027-01-31")
        sys.register_instances(
            STAFF["producer"], proposal_id="p-scarf",
            instances=[
                {"instance_id": "i-1", "state": "not_produced", "quantity": 50},
                {"instance_id": "i-2", "state": "in_transit", "quantity": 20},
                {"instance_id": "i-3", "state": "sold", "quantity": 30},
                {"instance_id": "i-4", "state": "promo_only", "quantity": 4},
            ],
        )
        # 已放行 300 件后改期：10 月开幕推迟到 2027-03-01，超出当前许可期限
        result = sys.reschedule_exhibition(
            STAFF["curator"], exhibition_id="exh-2026-modern",
            new_opens_on="2027-03-01", new_closes_on="2027-05-31",
        )
        by_state = actions(result, Trigger.OPENING_RESCHEDULED)
        self.assertEqual(by_state["not_produced"], DispositionAction.RESCHEDULE_HOLD.value)
        self.assertEqual(by_state["in_transit"], DispositionAction.RESCHEDULE_HOLD.value)
        self.assertEqual(by_state["sold"], DispositionAction.HONOR_SOLD_COPIES.value)
        self.assertEqual(
            by_state["promo_only"],
            DispositionAction.RESCHEDULE_REVALIDATE.value,
        )
        # 新档期下继续量产被阻断：先重启权利/经营门
        from museum_collab import ReleaseBlocked
        with self.assertRaisesRegex(ReleaseBlocked, "许可期限"):
            sys.release_production(
                STAFF["business"], proposal_id="p-scarf", version_id=v,
                quantity=10, channel="museum-shop", region="CN",
            )

    def test_slight_reschedule_within_term_allows_promo_continue(self) -> None:
        sys, _ = self._approved_proposal()
        sys.register_instances(
            STAFF["producer"], proposal_id="p-scarf",
            instances=[{"instance_id": "i-p", "state": "promo_only", "quantity": 2}],
        )
        result = sys.reschedule_exhibition(
            STAFF["curator"], exhibition_id="exh-2026-modern",
            new_opens_on="2026-10-15", new_closes_on="2026-12-20",
        )
        by_state = actions(result, Trigger.OPENING_RESCHEDULED)
        self.assertEqual(by_state["promo_only"], DispositionAction.PROMO_CONTINUE.value)


def standard_license_until(valid_until: str):
    from museum_collab import LicenseScope, Purpose
    return LicenseScope(
        max_quantity=2000, regions=frozenset({"CN"}),
        channels=frozenset({"museum-shop", "online-mall"}),
        valid_from="2026-08-01", valid_until=valid_until,
        purpose=Purpose.COMMERCIAL,
    )


if __name__ == "__main__":
    unittest.main()
