"""生产工单钉住批准版本哈希；实例状态机；撤回/改期分类处置不一刀切。"""
import unittest

from museum_collab.actors import Gate
from museum_collab.errors import StateError
from museum_collab.events import Action, Trigger
from museum_collab.licensing import Channel
from museum_collab.production import InstanceState

from fixtures_support import (
    approve_all, fresh_platform, build_actors, make_proposal,
    standard_license_kwargs,
)


class ProductionTest(unittest.TestCase):
    def setUp(self) -> None:
        self.p = fresh_platform()
        self.a = build_actors()
        self.pid = make_proposal(self.p, self.a["designer"], quantity=10)
        self.p.grant_license(self.a["rights"], **standard_license_kwargs())
        approve_all(self.p, self.pid, self.a)
        self.contract = self.p.record_contract(
            self.a["admin"], "partner-A", "丝巾采购合同",
            body="合同正文：……", body_ref="sealed://contracts/c1")

    def _order(self, quantity: int = 10) -> str:
        return self.p.create_production_order(
            self.a["ops"], proposal_id=self.pid, region="CN",
            channel=Channel.MUSEUM_SHOP, quantity=quantity,
            on_date="2026-09-20", contract_id=self.contract.contract_id)

    def test_order_pins_approved_hash_and_rights_snapshot(self) -> None:
        order_id = self._order()
        order = self.p.orders[order_id]
        approved_hash = self.p.proposals[self.pid].versions[1].content_sha256
        self.assertEqual(order.version_sha256, approved_hash)
        self.assertTrue(order.rights_snapshot.snapshot_sha256)
        self.assertEqual(order.contract_sha256,
                         self.contract.body_sha256)
        self.assertIn(
            self.p.active_licenses_for(self.pid)[0].license_id,
            order.license_ids)

    def test_instance_state_machine_rejects_illegal_jump(self) -> None:
        order_id = self._order(quantity=1)
        inst = f"{order_id}-item-0001"
        with self.assertRaises(StateError):
            # 未生产不能直接跳到已售
            self.p.transition_instance(
                self.a["ops"], inst, InstanceState.SOLD,
                on_date="2026-09-25")

    def test_happy_path_unproduced_to_sold(self) -> None:
        order_id = self._order(quantity=1)
        inst = f"{order_id}-item-0001"
        self.p.transition_instance(
            self.a["ops"], inst, InstanceState.IN_TRANSIT,
            on_date="2026-09-22")
        self.p.transition_instance(
            self.a["ops"], inst, InstanceState.SOLD,
            on_date="2026-09-28")
        self.assertEqual(self.p.instances[inst].state, InstanceState.SOLD)


class EventDispositionTest(unittest.TestCase):
    def setUp(self) -> None:
        self.p = fresh_platform()
        self.a = build_actors()
        self.pid = make_proposal(self.p, self.a["designer"], quantity=4)
        self.license_id = self.p.grant_license(
            self.a["rights"], **standard_license_kwargs())
        approve_all(self.p, self.pid, self.a)
        contract = self.p.record_contract(
            self.a["admin"], "partner-A", "丝巾采购合同",
            body="合同正文：……", body_ref="sealed://contracts/c1")
        self.order_id = self.p.create_production_order(
            self.a["ops"], proposal_id=self.pid, region="CN",
            channel=Channel.MUSEUM_SHOP, quantity=4,
            on_date="2026-09-20", contract_id=contract.contract_id)
        self.items = [f"{self.order_id}-item-{i:04d}" for i in range(1, 5)]

    def _distribute_into_four_states(self) -> None:
        # item1 未生产；item2 在途；item3 已售；item4 转展览宣传
        self.p.transition_instance(
            self.a["ops"], self.items[1], InstanceState.IN_TRANSIT,
            on_date="2026-09-22")
        self.p.transition_instance(
            self.a["ops"], self.items[2], InstanceState.IN_TRANSIT,
            on_date="2026-09-22")
        self.p.transition_instance(
            self.a["ops"], self.items[2], InstanceState.SOLD,
            on_date="2026-09-28")
        self.p.transition_instance(
            self.a["ops"], self.items[3], InstanceState.EXHIBITION_PROMO,
            on_date="2026-09-23")

    def test_revocation_generates_different_action_per_state(self) -> None:
        self._distribute_into_four_states()
        self.p.revoke_license(self.a["holder"], self.license_id,
                              on_date="2026-09-29", reason="权利人撤回")
        actions = {}
        for item in self.items:
            ds = self.p.dispositions_for_instance(item)
            self.assertEqual(len(ds), 1)
            actions[ds[0].state_at_event] = ds[0].action
        self.assertEqual(actions[InstanceState.UNPRODUCED],
                         Action.CANCEL_PRODUCTION)
        self.assertEqual(actions[InstanceState.IN_TRANSIT],
                         Action.RECALL_AND_HOLD)
        self.assertEqual(actions[InstanceState.SOLD],
                         Action.POST_SALE_NOTICE)
        self.assertEqual(actions[InstanceState.EXHIBITION_PROMO],
                         Action.TAKE_DOWN_PROMO)

    def test_revocation_does_not_rewrite_history_but_freezes_future(self) -> None:
        self._distribute_into_four_states()
        self.p.revoke_license(self.a["holder"], self.license_id,
                              on_date="2026-09-29", reason="权利人撤回")
        # 已售事实不变
        self.assertEqual(self.p.instances[self.items[2]].state,
                         InstanceState.SOLD)
        # 台账中原来的批准、生产记录仍在
        kinds = [e.kind for e in self.p.ledger.entries]
        self.assertIn("approval_granted", kinds)
        self.assertIn("production_order_created", kinds)
        # 在途货物被冻结，不能继续销售
        with self.assertRaises(StateError):
            self.p.transition_instance(
                self.a["ops"], self.items[1], InstanceState.SOLD,
                on_date="2026-09-30")

    def test_reschedule_within_license_term_holds_unproduced_and_intransit(
            self) -> None:
        self._distribute_into_four_states()
        # 许可到 2027-06-30，新档期仍在期限内
        self.p.reschedule_exhibition(
            self.a["curator"], "exhibition-modern-2026",
            new_opens_on="2026-11-15", new_closes_on="2027-01-20",
            on_date="2026-09-15")
        by_state = {}
        for item in self.items:
            d = self.p.dispositions_for_instance(item)[-1]
            by_state[d.state_at_event] = d.action
        self.assertEqual(by_state[InstanceState.UNPRODUCED],
                         Action.HOLD_PRODUCTION)
        self.assertEqual(by_state[InstanceState.IN_TRANSIT],
                         Action.HOLD_DISTRIBUTION)
        self.assertEqual(by_state[InstanceState.SOLD],
                         Action.KEEP_SOLD_RECORD)
        self.assertEqual(by_state[InstanceState.EXHIBITION_PROMO],
                         Action.UPDATE_PROMO_DATES)

    def test_reschedule_beyond_license_term_escalates_renewal(self) -> None:
        # 许可 2026-12-31 到期，开幕推迟到 2027-03 月
        p = fresh_platform()
        a = build_actors()
        pid = make_proposal(p, a["designer"], quantity=1)
        p.grant_license(a["rights"], **standard_license_kwargs(
            ends_on="2026-12-31"))
        approve_all(p, pid, a)
        contract = p.record_contract(
            a["admin"], "partner-A", "丝巾采购合同",
            body="合同正文：……", body_ref="sealed://contracts/c1")
        order_id = p.create_production_order(
            a["ops"], proposal_id=pid, region="CN",
            channel=Channel.MUSEUM_SHOP, quantity=1,
            on_date="2026-09-20", contract_id=contract.contract_id)
        p.reschedule_exhibition(
            a["curator"], "exhibition-modern-2026",
            new_opens_on="2027-03-01", new_closes_on="2027-05-01",
            on_date="2026-09-15")
        d = p.dispositions_for_instance(f"{order_id}-item-0001")[0]
        self.assertEqual(d.action, Action.ESCALATE_RIGHTS_RENEWAL)
        self.assertFalse(d.context["license_covers_new_window"])
        # 续权未完成前恢复工单必须继续阻断
        with self.assertRaises(Exception):
            p.resume_order(a[Gate.BUSINESS], order_id,
                           on_date="2027-02-01", reason="rights_renewed")


if __name__ == "__main__":
    unittest.main()
