"""信息隔离：合作方只见自身环节；合同正文不对普通运营开放。"""
import unittest

from museum_collab.actors import Gate, Role
from museum_collab.errors import AuthorizationError
from museum_collab.licensing import Channel
from museum_collab.permissions import Stage
from museum_collab.production import InstanceState

from fixtures_support import (
    approve_all, fresh_platform, build_actors, make_proposal,
    standard_license_kwargs,
)


class PermissionTest(unittest.TestCase):
    def setUp(self) -> None:
        self.p = fresh_platform()
        self.a = build_actors()
        self.pid = make_proposal(self.p, self.a["designer"], quantity=5)
        self.p.grant_license(self.a["rights"], **standard_license_kwargs())
        approve_all(self.p, self.pid, self.a)
        self.contract = self.p.record_contract(
            self.a["admin"], "partner-A", "丝巾采购合同",
            body="甲方委托乙方秘密条款……", body_ref="sealed://contracts/c1")
        self.order_id = self.p.create_production_order(
            self.a["ops"], proposal_id=self.pid, region="CN",
            channel=Channel.MUSEUM_SHOP, quantity=5,
            on_date="2026-09-20", contract_id=self.contract.contract_id)

    def test_partner_cannot_see_other_partners_proposal(self) -> None:
        with self.assertRaises(AuthorizationError):
            self.p.partner_view(self.a["pB"], self.pid)

    def test_creative_partner_sees_design_but_not_production_detail(self) -> None:
        # A 的环节是 creative + distribution，不含 production
        view = self.p.partner_view(self.a["pA"], self.pid)
        stages = {s["stage"] for s in view["sections"]}
        self.assertIn(Stage.CREATIVE, stages)
        self.assertNotIn(Stage.PRODUCTION, stages)
        self.assertIn(Stage.DISTRIBUTION, stages)

    def test_production_partner_sees_specs_but_not_holder_identity(self) -> None:
        # 让 B 成为该提案的生产合作方：给 B 一份非独占许可与对应合同、
        # 再用 B 的合作方建单。这里直接验证 B 在自己的工单视图里
        # 看不到权利人身份与审批内情。
        pid_b = make_proposal(self.p, self.a["pB"], partner_id="partner-B")
        self.p.grant_license(self.a["rights"], **standard_license_kwargs(
            partner_id="partner-B"))
        approve_all(self.p, pid_b, self.a)
        contract_b = self.p.record_contract(
            self.a["admin"], "partner-B", "围巾代工合同",
            body="代工合同正文", body_ref="sealed://contracts/cb")
        self.p.create_production_order(
            self.a["pB"], proposal_id=pid_b, region="CN",
            channel=Channel.MUSEUM_SHOP, quantity=3,
            on_date="2026-09-20", contract_id=contract_b.contract_id)
        view = self.p.partner_view(self.a["pB"], pid_b)
        stages = {s["stage"] for s in view["sections"]}
        self.assertEqual(stages, {Stage.CREATIVE, Stage.PRODUCTION})
        prod_section = next(s for s in view["sections"]
                            if s["stage"] == Stage.PRODUCTION)
        flat = repr(prod_section)
        self.assertNotIn("holder-artist", flat)
        self.assertNotIn("家属代理", flat)

    def test_messages_scoped_to_audience(self) -> None:
        self.p.post_message(
            self.a["curator"], self.pid, "请 A 确认打样排期",
            audience=("partner-A",))
        self.p.post_message(
            self.a["curator"], self.pid, "内部：权利人底线价 X",
            audience=(Role.RIGHTS_OFFICER,))
        a_view = self.p.partner_view(self.a["pA"], self.pid)
        contents = [m["content"] for m in a_view["messages"]]
        self.assertIn("请 A 确认打样排期", contents)
        self.assertNotIn("内部：权利人底线价 X", contents)

    def test_contract_meta_never_contains_body(self) -> None:
        for actor in (self.a["ops"], self.a["curator"], self.a[Gate.BUSINESS]):
            meta = self.p.contract_meta(actor, self.contract.contract_id)
            self.assertIsNone(meta["body"])
            self.assertTrue(meta["body_sha256"])

    def test_operations_cannot_open_contract_body(self) -> None:
        sealed = {"sealed://contracts/c1": "甲方委托乙方秘密条款……"}
        with self.assertRaises(AuthorizationError):
            self.p.contract_body(self.a["ops"], self.contract.contract_id,
                                 sealed)

    def test_authorized_role_opens_body_and_hash_is_checked(self) -> None:
        sealed_ok = {"sealed://contracts/c1": "甲方委托乙方秘密条款……"}
        body = self.p.contract_body(
            self.a[Gate.RIGHTS], self.contract.contract_id, sealed_ok)
        self.assertIn("秘密条款", body)
        # 受控存储里的正文被掉包：哈希校验失败
        sealed_bad = {"sealed://contracts/c1": "被替换的正文"}
        from museum_collab.errors import ValidationError
        with self.assertRaises(ValidationError):
            self.p.contract_body(
                self.a[Gate.RIGHTS], self.contract.contract_id, sealed_bad)


if __name__ == "__main__":
    unittest.main()
