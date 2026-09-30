"""四类审批门角色分立；按变更范围只重启必要审核；旧版本不得流入生产。"""
import unittest

from museum_collab.actors import Gate
from museum_collab.errors import (
    ApprovalRequiredError, AuthorizationError, LicenseError,
)

from fixtures_support import (
    approve_all, fresh_platform, build_actors, make_proposal,
    standard_license_kwargs,
)


class ApprovalFlowTest(unittest.TestCase):
    def setUp(self) -> None:
        self.p = fresh_platform()
        self.a = build_actors()

    def test_each_gate_only_its_role_can_approve(self) -> None:
        pid = make_proposal(self.p, self.a["designer"])
        # 经营角色不能替学术审核签字
        with self.assertRaises(AuthorizationError):
            self.p.approve(self.a[Gate.BUSINESS], pid, Gate.ACADEMIC)
        # 没有许可时，权利确认必须被系统阻断，而不是靠人自觉
        with self.assertRaises(LicenseError):
            self.p.approve(self.a[Gate.RIGHTS], pid, Gate.RIGHTS)

    def test_production_blocked_until_all_four_gates_pass(self) -> None:
        pid = make_proposal(self.p, self.a["designer"])
        self.p.grant_license(self.a["rights"], **standard_license_kwargs())
        self.p.approve(self.a[Gate.ACADEMIC], pid, Gate.ACADEMIC)
        with self.assertRaises(ApprovalRequiredError) as ctx:
            self.p.create_production_order(
                self.a["ops"], proposal_id=pid, region="CN",
                channel="museum_shop", quantity=100, on_date="2026-09-15",
                contract_id="missing")
        missing = set(ctx.exception.missing)
        self.assertIn(Gate.RIGHTS, missing)
        self.assertIn(Gate.PUBLIC_BENEFIT, missing)
        self.assertIn(Gate.BUSINESS, missing)

    def test_channel_change_reopens_only_rights_and_business(self) -> None:
        pid = make_proposal(self.p, self.a["designer"])
        self.p.grant_license(self.a["rights"], **standard_license_kwargs(
            channels=["museum_shop", "online_store", "offline_event"]))
        approve_all(self.p, pid, self.a)

        v2 = self.p.revise_proposal(self.a["designer"], pid, {
            "channels": ["museum_shop", "online_store", "offline_event"]})
        version = self.p.proposals[pid].versions[v2]
        # 学术、公益不受渠道影响：旧批准显式沿用
        self.assertIn(Gate.ACADEMIC, version.approvals)
        self.assertIn(Gate.PUBLIC_BENEFIT, version.approvals)
        self.assertEqual(
            version.approvals[Gate.ACADEMIC].carried_from, 1)
        # 权利与经营必须重审
        self.assertNotIn(Gate.RIGHTS, version.approvals)
        self.assertNotIn(Gate.BUSINESS, version.approvals)

    def test_typo_fix_reopens_no_gate(self) -> None:
        pid = make_proposal(self.p, self.a["designer"])
        self.p.grant_license(self.a["rights"], **standard_license_kwargs())
        approve_all(self.p, pid, self.a)
        v2 = self.p.revise_proposal(self.a["designer"], pid, {
            "notes": "订正一个错别字"})
        version = self.p.proposals[pid].versions[v2]
        self.assertEqual(set(version.approvals), set(Gate.ALL))
        # 全部为沿用，但仍是可追溯的新版本
        self.assertTrue(all(r.carried_from == 1
                            for r in version.approvals.values()))

    def test_material_change_does_not_touch_rights_gate(self) -> None:
        pid = make_proposal(self.p, self.a["designer"])
        self.p.grant_license(self.a["rights"], **standard_license_kwargs())
        approve_all(self.p, pid, self.a)
        v2 = self.p.revise_proposal(self.a["designer"], pid,
                                    {"material": "仿丝涤纶"})
        version = self.p.proposals[pid].versions[v2]
        self.assertIn(Gate.RIGHTS, version.approvals)      # 权利沿用
        self.assertIn(Gate.PUBLIC_BENEFIT, version.approvals)
        self.assertNotIn(Gate.ACADEMIC, version.approvals)  # 学术重审
        self.assertNotIn(Gate.BUSINESS, version.approvals)  # 经营重审

    def test_pattern_swap_reopens_academic_and_rights(self) -> None:
        pid = make_proposal(self.p, self.a["designer"])
        self.p.grant_license(self.a["rights"], **standard_license_kwargs())
        approve_all(self.p, pid, self.a)
        v2 = self.p.revise_proposal(self.a["designer"], pid, {
            "element_uses": [{
                "artwork_id": "artwork-river-17",
                "element_id": "el-whole",
                "adaptation": "full_image_print"}]})
        version = self.p.proposals[pid].versions[v2]
        self.assertNotIn(Gate.ACADEMIC, version.approvals)
        self.assertNotIn(Gate.RIGHTS, version.approvals)
        # 换图案后，即便旧版曾全通过，生产也拿不到"已批准版本"
        with self.assertRaises(ApprovalRequiredError):
            self.p.create_production_order(
                self.a["ops"], proposal_id=pid, region="CN",
                channel="museum_shop", quantity=10, on_date="2026-09-15",
                contract_id="contract-1")


if __name__ == "__main__":
    unittest.main()
